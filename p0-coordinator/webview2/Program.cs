using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace AgentGuildWebView2
{
    internal static class Program
    {
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
        private static Form _form;
        private static WebView2 _webView;
        private static string _p0Root;
        private static bool _smoke;
        private static bool _smokeDone;
        private static string _screenshotPath;
        private static bool _screenshotDone;
        private static string _dumpTextPath;
        private static bool _dumpTextDone;

        [STAThread]
        private static void Main(string[] args)
        {
            _smoke = Array.IndexOf(args, "--smoke") >= 0;
            _screenshotPath = ArgValue(args, "--screenshot");
            _dumpTextPath = ArgValue(args, "--dump-text");
            _p0Root = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", ".."));

            string call = ArgValue(args, "--call");
            if (!string.IsNullOrEmpty(call))
            {
                try
                {
                    Dictionary<string, object> payload = new Dictionary<string, object>();
                    AddIf(payload, "candidateId", ArgValue(args, "--candidate"));
                    AddIf(payload, "taskId", ArgValue(args, "--task"));
                    AddIf(payload, "repoPath", ArgValue(args, "--repo-path"));
                    AddIf(payload, "worktreePath", ArgValue(args, "--worktree"));
                    AddIf(payload, "agent", ArgValue(args, "--agent"));
                    AddIf(payload, "check", ArgValue(args, "--check"));
                    AddIf(payload, "prompt", ArgValue(args, "--prompt"));
                    object data = RunCommand(call, payload);
                    Console.WriteLine(Json.Serialize(data));
                    Environment.ExitCode = 0;
                }
                catch (Exception ex)
                {
                    Console.WriteLine("CALL_FAIL " + ex.Message);
                    Environment.ExitCode = 1;
                }
                return;
            }

            string renderer = Path.Combine(_p0Root, "electron", "renderer");

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            _form = new Form();
            _form.Text = "Agent Guild 控制台";
            _form.Width = 1280;
            _form.Height = 900;
            _form.MinimumSize = new Size(1000, 700);
            _form.StartPosition = FormStartPosition.CenterScreen;
            _webView = new WebView2();
            _webView.Dock = DockStyle.Fill;
            _form.Controls.Add(_webView);
            _form.Shown += async (sender, e) => { await InitAsync(renderer); };
            _form.FormClosed += delegate { Application.Exit(); };

            string log = Path.Combine(_p0Root, ".tmp", "webview2-ui.log");
            try { File.AppendAllText(log, "START\n"); } catch { }
            Application.ThreadException += (sender, e) =>
            {
                try { File.AppendAllText(log, "THREAD_EXCEPTION\n" + e.Exception + "\n"); } catch { }
                MessageBox.Show(e.Exception.Message, "Agent Guild 错误");
            };
            AppDomain.CurrentDomain.UnhandledException += (sender, e) =>
            {
                try { File.AppendAllText(log, "UNHANDLED\n" + e.ExceptionObject + "\n"); } catch { }
            };
            _form.Show();
            try { File.AppendAllText(log, "FORM_SHOWN\n"); } catch { }
            Application.Run();
            try { File.AppendAllText(log, "APP_EXIT\n"); } catch { }
        }

        private static async Task InitAsync(string renderer)
        {
            try
            {
                string userData = Path.Combine(_p0Root, ".tmp", "webview2-userdata");
                Directory.CreateDirectory(userData);
                CoreWebView2Environment env = await CoreWebView2Environment.CreateAsync(null, userData);
                await _webView.EnsureCoreWebView2Async(env);

                _webView.CoreWebView2.SetVirtualHostNameToFolderMapping(
                    "guild.local", renderer, CoreWebView2HostResourceAccessKind.Allow);
                _webView.CoreWebView2.WebMessageReceived += OnMessage;
                await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(BridgeScript());
                _webView.CoreWebView2.NavigationCompleted += async (sender, e) =>
                {
                    if (_smoke && !_smokeDone)
                    {
                        _smokeDone = true;
                        string js = "window.guild.rpc('projects').then(function(r){"
                            + "window.chrome.webview.postMessage({smoke:true,data:r});"
                            + "}).catch(function(e){window.chrome.webview.postMessage({smoke:false,error:String(e)});});";
                        await _webView.CoreWebView2.ExecuteScriptAsync(js);
                    }
                    if (!string.IsNullOrEmpty(_screenshotPath) && !_screenshotDone)
                    {
                        _screenshotDone = true;
                        await Task.Delay(1800);
                        using (FileStream fs = File.Create(_screenshotPath))
                        {
                            await _webView.CoreWebView2.CapturePreviewAsync(
                                CoreWebView2CapturePreviewImageFormat.Png, fs);
                        }
                        Console.WriteLine("WEBVIEW2_SCREENSHOT " + _screenshotPath);
                        _form.Close();
                    }
                    if (!string.IsNullOrEmpty(_dumpTextPath) && !_dumpTextDone)
                    {
                        _dumpTextDone = true;
                        await Task.Delay(1800);
                        string dumpJs = "JSON.stringify({text:document.body.innerText,"
                            + "error:(document.getElementById('error')||{}).innerText||'',"
                            + "notice:(document.getElementById('notice')||{}).innerText||'',"
                            + "lastError:window.__lastError||'',"
                            + "check:(document.getElementById('check')||{}).value||'',"
                            + "repo:(document.getElementById('repo')||{}).value||'',"
                            + "worktree:(document.getElementById('worktree')||{}).value||''})";
                        string text = await _webView.CoreWebView2.ExecuteScriptAsync(dumpJs);
                        File.WriteAllText(_dumpTextPath, text);
                        Console.WriteLine("WEBVIEW2_TEXT " + _dumpTextPath);
                        _form.Close();
                    }
                };
                _webView.CoreWebView2.Navigate("https://guild.local/index.html");
            }
            catch (Exception ex)
            {
                Console.WriteLine("WEBVIEW2_INIT_FAIL " + ex);
                Environment.ExitCode = 1;
                _form.Close();
            }
        }

        private static string BridgeScript()
        {
            return "(function(){"
                + "var pending={};var seq=0;"
                + "window.guild={rpc:function(cmd,payload){return new Promise(function(resolve,reject){"
                + "var id=++seq;pending[id]={resolve:resolve,reject:reject};"
                + "window.chrome.webview.postMessage({id:id,cmd:cmd,payload:payload||{}});"
                + "});}};"
                + "window.chrome.webview.addEventListener('message',function(e){"
                + "var m=e.data;var p=pending[m.id];if(!p)return;delete pending[m.id];"
                + "if(m.ok){p.resolve(m.data);}else{p.reject(new Error(m.error||'rpc failed'));}"
                + "});"
                + "})();";
        }

        private static void OnMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            try
            {
                Dictionary<string, object> msg =
                    Json.Deserialize<Dictionary<string, object>>(e.WebMessageAsJson);
                if (msg.ContainsKey("smoke"))
                {
                    Console.WriteLine("WEBVIEW2_SMOKE " + Json.Serialize(msg));
                    bool ok = msg.ContainsKey("smoke") && Convert.ToBoolean(msg["smoke"]);
                    Environment.ExitCode = ok ? 0 : 1;
                    _form.BeginInvoke(new Action(delegate { _form.Close(); }));
                    return;
                }

                object id = msg.ContainsKey("id") ? msg["id"] : null;
                string cmd = msg.ContainsKey("cmd") ? Convert.ToString(msg["cmd"]) : "";
                Dictionary<string, object> payload = null;
                if (msg.ContainsKey("payload") && msg["payload"] is Dictionary<string, object>)
                {
                    payload = (Dictionary<string, object>)msg["payload"];
                }

                Dictionary<string, object> response = new Dictionary<string, object>();
                response["id"] = id;
                try
                {
                    object data = RunCommand(cmd, payload);
                    response["ok"] = true;
                    response["data"] = data;
                }
                catch (Exception ex)
                {
                    response["ok"] = false;
                    response["error"] = ex.Message;
                }
                _webView.CoreWebView2.PostWebMessageAsJson(Json.Serialize(response));
            }
            catch (Exception ex)
            {
                Console.WriteLine("WEBVIEW2_MESSAGE_FAIL " + ex);
            }
        }

        private static object RunCommand(string cmd, Dictionary<string, object> payload)
        {
            List<string> args = new List<string>();
            if (cmd == "projects")
            {
                args.Add("projects");
            }
            else if (cmd == "adapters")
            {
                args.Add("adapters");
            }
            else if (cmd == "candidates")
            {
                args.Add("candidates");
            }
            else if (cmd == "runs")
            {
                args.Add("runs");
                args.Add("--limit");
                args.Add("50");
            }
            else if (cmd == "demo_paths")
            {
                Dictionary<string, object> demo = new Dictionary<string, object>();
                demo["repo"] = Path.Combine(_p0Root, ".tmp", "agent-demo", "repo");
                demo["worktree"] = Path.Combine(_p0Root, ".tmp", "agent-demo", "integration");
                demo["check"] = "python -m py_compile src/app.py";
                return demo;
            }
            else if (cmd == "start_run")
            {
                string taskId = GetString(payload, "taskId", true);
                string agent = GetString(payload, "agent", false);
                if (string.IsNullOrEmpty(agent)) agent = "dsh";
                args.Add("start-agent");
                args.Add("--task");
                args.Add(taskId);
                args.Add("--agent");
                args.Add(agent);
                string prompt = GetString(payload, "prompt", false);
                if (!string.IsNullOrEmpty(prompt))
                {
                    args.Add("--prompt");
                    args.Add(prompt);
                }
                string check = GetString(payload, "check", false);
                if (!string.IsNullOrEmpty(check))
                {
                    args.Add("--check");
                    args.Add(check);
                }
            }
            else if (cmd == "approve")
            {
                string candidateId = GetString(payload, "candidateId", true);
                string approver = GetString(payload, "approver", false);
                if (string.IsNullOrEmpty(approver)) approver = "webview2-reviewer";
                args.Add("approve-candidate");
                args.Add("--candidate");
                args.Add(candidateId);
                args.Add("--approver");
                args.Add(approver);
                string role = GetString(payload, "role", false);
                if (string.IsNullOrEmpty(role)) role = "reviewer";
                args.Add("--role");
                args.Add(role);
                string note = GetString(payload, "note", false);
                if (!string.IsNullOrEmpty(note))
                {
                    args.Add("--note");
                    args.Add(note);
                }
            }
            else if (cmd == "merge")
            {
                string candidateId = GetString(payload, "candidateId", true);
                string repoPath = GetString(payload, "repoPath", true);
                string worktreePath = GetString(payload, "worktreePath", true);
                string approver = GetString(payload, "approver", false);
                if (string.IsNullOrEmpty(approver)) approver = "webview2-reviewer";
                args.Add("merge-candidate-git");
                args.Add("--candidate");
                args.Add(candidateId);
                args.Add("--repo-path");
                args.Add(repoPath);
                args.Add("--worktree");
                args.Add(worktreePath);
                args.Add("--requested-by");
                args.Add(approver);
                string check = GetString(payload, "check", false);
                if (!string.IsNullOrEmpty(check))
                {
                    args.Add("--check");
                    args.Add(check);
                }
                args.Add("--finish");
            }
            else if (cmd == "status")
            {
                string projectId = payload != null && payload.ContainsKey("projectId")
                    ? Convert.ToString(payload["projectId"]) : "";
                if (string.IsNullOrEmpty(projectId)) throw new Exception("projectId required");
                args.Add("status");
                args.Add("--project");
                args.Add(projectId);
            }
            else
            {
                throw new Exception("unknown command: " + cmd);
            }

            string output = RunPythonCli(args);
            return Json.DeserializeObject(output);
        }

        private static string RunPythonCli(List<string> args)
        {
            string python = Environment.GetEnvironmentVariable("PYTHON");
            if (string.IsNullOrEmpty(python)) python = "python";
            List<string> full = new List<string>();
            string db = Environment.GetEnvironmentVariable("GUILD_DB");
            if (!string.IsNullOrEmpty(db))
            {
                full.Add("--db");
                full.Add(db);
            }
            full.AddRange(args);

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = python;
            psi.WorkingDirectory = _p0Root;
            psi.UseShellExecute = false;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.CreateNoWindow = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;

            StringBuilder all = new StringBuilder();
            all.Append(Quote(Path.Combine(_p0Root, "worker_cli.py")));
            foreach (string a in full) all.Append(" ").Append(Quote(a));
            psi.Arguments = all.ToString();

            using (Process p = Process.Start(psi))
            {
                string stdout = p.StandardOutput.ReadToEnd();
                string stderr = p.StandardError.ReadToEnd();
                p.WaitForExit();
                if (p.ExitCode != 0)
                {
                    throw new Exception(string.IsNullOrEmpty(stderr) ? stdout : stderr);
                }
                return stdout;
            }
        }

        private static string ArgValue(string[] args, string name)
        {
            int i = Array.IndexOf(args, name);
            if (i < 0 || i + 1 >= args.Length) return "";
            return args[i + 1];
        }

        private static void AddIf(Dictionary<string, object> payload, string key, string value)
        {
            if (!string.IsNullOrEmpty(value)) payload[key] = value;
        }

        private static string GetString(Dictionary<string, object> payload, string key, bool required)
        {
            string value = payload != null && payload.ContainsKey(key)
                ? Convert.ToString(payload[key]) : "";
            if (required && string.IsNullOrEmpty(value))
            {
                throw new Exception(key + " required");
            }
            return value;
        }

        private static string Quote(string value)
        {
            if (value == null) return "\"\"";
            if (value.IndexOf(' ') < 0 && value.IndexOf('"') < 0) return value;
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }
    }
}
