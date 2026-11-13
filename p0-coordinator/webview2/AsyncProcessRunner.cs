using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace AgentGuildWebView2
{
    // One request owns one JobHost, which owns the complete Windows process tree.
    internal static class AsyncProcessRunner
    {
        internal const int OutputLimit = 2 * 1024 * 1024;

        internal static string Quote(string value)
        {
            var result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char ch in value ?? "")
            {
                if (ch == '\\') { slashes++; continue; }
                result.Append('\\', ch == '"' ? slashes * 2 + 1 : slashes);
                result.Append(ch); slashes = 0;
            }
            result.Append('\\', slashes * 2);
            return result.Append('"').ToString();
        }

        internal static string ResolveExecutable(string executable)
        {
            if (Path.IsPathRooted(executable)) return Path.GetFullPath(executable);
            foreach (string directory in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
            {
                string candidate = Path.Combine(directory.Trim('"'), executable);
                if (File.Exists(candidate)) return Path.GetFullPath(candidate);
                if (File.Exists(candidate + ".exe")) return Path.GetFullPath(candidate + ".exe");
            }
            throw new FileNotFoundException("Executable not found", executable);
        }

        private static void Kill(Process process)
        {
            try { if (!process.HasExited) process.Kill(); }
            catch (InvalidOperationException) { }
            catch (System.ComponentModel.Win32Exception) { }
        }

        private static async Task<string> DrainAsync(StreamReader stream)
        {
            char[] buffer = new char[4096];
            var text = new StringBuilder();
            int count;
            while ((count = await stream.ReadAsync(buffer, 0, buffer.Length).ConfigureAwait(false)) != 0)
            {
                if (text.Length + count > OutputLimit)
                    throw new InvalidDataException("Process output exceeded 2 MiB characters; result is unknown");
                text.Append(buffer, 0, count);
            }
            return text.ToString();
        }

        private static void WriteReceipt(string path, string requestId, int pid, string started, string state)
        {
            // All string values are generated GUIDs, decimal FILETIME values or fixed states.
            string json = "{\"version\":1,\"ownership_version\":2,\"request_id\":\"" + requestId
                + "\",\"host_pid\":" + pid + ",\"host_started\":\"" + started
                + "\",\"state\":\"" + state + "\"}";
            string temp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllText(temp, json, new UTF8Encoding(false));
                if (File.Exists(path)) File.Replace(temp, path, null);
                else File.Move(temp, path);
            }
            finally { if (File.Exists(temp)) File.Delete(temp); }
        }

        internal static async Task<string> RunAsync(string host, string executable, string[] args,
            string cwd, CancellationToken cancellation, TimeSpan timeout, string receiptDirectory = null)
        {
            cancellation.ThrowIfCancellationRequested();
            var command = new StringBuilder();
            command.Append(Process.GetCurrentProcess().Id).Append(' ').Append(Quote(ResolveExecutable(executable)));
            foreach (string arg in args) command.Append(' ').Append(Quote(arg));
            var info = new ProcessStartInfo(host, command.ToString()) {
                WorkingDirectory = cwd, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            string receiptPath = null, requestId = Guid.NewGuid().ToString("N");
            if (receiptDirectory != null)
            {
                receiptDirectory = Path.GetFullPath(receiptDirectory);
                Directory.CreateDirectory(receiptDirectory);
                receiptPath = Path.Combine(receiptDirectory, requestId + ".json");
                WriteReceipt(receiptPath, requestId, 0, "", "starting");
                info.EnvironmentVariables["GUILD_REQUEST_ID"] = requestId;
                info.EnvironmentVariables["GUILD_REQUEST_RECEIPT"] = receiptPath;
            }
            using (var process = new Process { StartInfo = info, EnableRaisingEvents = true })
            using (var deadline = new CancellationTokenSource(timeout))
            using (var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellation, deadline.Token))
            {
                var exited = new TaskCompletionSource<bool>();
                var stop = new TaskCompletionSource<bool>();
                process.Exited += delegate { exited.TrySetResult(true); };
                process.Start();
                string hostStarted = "";
                string receiptState = "unknown";
                try
                {
                    hostStarted = process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString();
                    if (receiptPath != null) WriteReceipt(receiptPath, requestId, process.Id, hostStarted, "running");
                }
                catch { Kill(process); throw; }
                process.StandardInput.Close();
                var stdout = DrainAsync(process.StandardOutput);
                var stderr = DrainAsync(process.StandardError);
                // A failed drain must also stop a child blocked while writing its pipe.
                var stdoutGuard = stdout.ContinueWith(t => { if (t.IsFaulted) { Kill(process); stop.TrySetResult(true); } }, TaskScheduler.Default);
                var stderrGuard = stderr.ContinueWith(t => { if (t.IsFaulted) { Kill(process); stop.TrySetResult(true); } }, TaskScheduler.Default);
                using (linked.Token.Register(() => { Kill(process); stop.TrySetResult(true); }))
                {
                    try
                    {
                        if (await Task.WhenAny(exited.Task, stop.Task).ConfigureAwait(false) != exited.Task
                            && await Task.WhenAny(exited.Task, Task.Delay(5000)).ConfigureAwait(false) != exited.Task)
                            throw new TimeoutException("Could not confirm process termination; result is unknown");
                        var drains = Task.WhenAll(stdout, stderr);
                        if (await Task.WhenAny(drains, Task.Delay(5000)).ConfigureAwait(false) != drains)
                            throw new TimeoutException("Process exited but pipes did not close; result is unknown");
                        string[] output = await drains.ConfigureAwait(false);
                        await Task.WhenAll(stdoutGuard, stderrGuard).ConfigureAwait(false);
                        receiptState = cancellation.IsCancellationRequested || deadline.IsCancellationRequested ? "stopped" : "exited";
                        if (cancellation.IsCancellationRequested)
                            throw new OperationCanceledException("Local process tree stopped; business result is unknown", cancellation);
                        if (deadline.IsCancellationRequested)
                            throw new TimeoutException("Request timed out; business result is unknown");
                        if (process.ExitCode != 0)
                            throw new InvalidOperationException("Process exited (" + process.ExitCode + "): " +
                                (string.IsNullOrEmpty(output[1]) ? output[0] : output[1]));
                        return output[0];
                    }
                    finally
                    {
                        Kill(process);
                        // An incomplete receipt stays unknown; it never fabricates business completion.
                        if (receiptPath != null)
                        {
                            try { WriteReceipt(receiptPath, requestId, process.Id, hostStarted, receiptState); }
                            catch (IOException) { }
                            catch (UnauthorizedAccessException) { }
                        }
                    }
                }
            }
        }
    }
}
