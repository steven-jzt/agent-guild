using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace AgentGuildWebView2
{
    // Offline executable exercised by test_webview2_process.py. Not part of the GUI.
    internal static class ProcessRunnerHarness
    {
        private static async Task<int> Run(string[] args)
        {
            using (var cancel = new CancellationTokenSource())
            {
                string host = args[0], python = args[1], fixture = args[2], mode = args[3], pidFile = args[4];
                var run = AsyncProcessRunner.RunAsync(host, python, new[] { fixture, mode, pidFile, "中文 空格\"引号\\" },
                    Path.GetDirectoryName(fixture), cancel.Token, TimeSpan.FromSeconds(mode == "timeout" ? 2 : 15),
                    Path.Combine(Path.GetDirectoryName(fixture), "request-receipts"));
                if (mode == "parallel")
                {
                    var quick = AsyncProcessRunner.RunAsync(host, python, new[] { fixture, "echo", pidFile },
                        Path.GetDirectoryName(fixture), CancellationToken.None, TimeSpan.FromSeconds(10));
                    if (await Task.WhenAny(run, quick) != quick || run.IsCompleted)
                        throw new Exception("quick request was blocked by the long request");
                    await quick;
                }
                if (mode == "cancel" || mode == "recovery-cancel")
                {
                    for (int n = 0; !File.Exists(pidFile) && n < 1000; n++) await Task.Delay(10);
                    if (!File.Exists(pidFile)) throw new Exception("fixture did not start");
                    cancel.Cancel();
                }
                try { Console.WriteLine(await run); }
                catch (OperationCanceledException) { Console.WriteLine("cancelled; result unknown"); return 2; }
                return 0;
            }
        }

        private static int Main(string[] args)
        {
            Console.OutputEncoding = new System.Text.UTF8Encoding(false);
            try { return Run(args).GetAwaiter().GetResult(); }
            catch (Exception error) { Console.Error.WriteLine(error.Message); return 1; }
        }
    }
}
