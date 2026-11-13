using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading;
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
        private static bool _smokeRecovery;
        private static bool _smokeReview;
        private static string _screenshotPath;
        private static bool _screenshotDone;
        private static string _dumpTextPath;
        private static bool _dumpTextDone;
        private static readonly Dictionary<string, CancellationTokenSource> Requests = new Dictionary<string, CancellationTokenSource>();
        private static readonly HashSet<string> Mutations = new HashSet<string>();
        private static bool _closing;
        private static int _page;
        private static int _uiTicks;
        private static bool _uiResponsive;
        private static bool _smokeCancel;

        [STAThread]
        private static void Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            _smoke = Array.IndexOf(args, "--smoke") >= 0;
            _smokeCancel = Array.IndexOf(args, "--smoke-cancel") >= 0;
            _smokeRecovery = Array.IndexOf(args, "--smoke-recovery") >= 0;
            _smokeReview = Array.IndexOf(args, "--smoke-review") >= 0;
            _smoke = _smoke || _smokeCancel || _smokeRecovery || _smokeReview;
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
                    AddIf(payload, "checkId", ArgValue(args, "--check-id"));
                    AddIf(payload, "timeout", ArgValue(args, "--timeout"));
                    AddIf(payload, "taskId", ArgValue(args, "--task"));
                    AddIf(payload, "repoPath", ArgValue(args, "--repo-path"));
                    AddIf(payload, "worktreePath", ArgValue(args, "--worktree"));
                    AddIf(payload, "agent", ArgValue(args, "--agent"));
                    AddIf(payload, "check", ArgValue(args, "--check"));
                    AddIf(payload, "prompt", ArgValue(args, "--prompt"));
                    AddIf(payload, "targetRef", ArgValue(args, "--target-ref"));
                    AddIf(payload, "projectId", ArgValue(args, "--project"));
                    AddIf(payload, "executionId", ArgValue(args, "--execution"));
                    AddIf(payload, "generation", ArgValue(args, "--generation"));
                    AddIf(payload, "reason", ArgValue(args, "--reason"));
                    AddIf(payload, "attemptId", ArgValue(args, "--attempt"));
                    AddIf(payload, "action", ArgValue(args, "--action"));
                    AddIf(payload, "resultSha", ArgValue(args, "--result-sha"));
                    payload["confirmLocalStopped"] = Array.IndexOf(args, "--confirm-local-stopped") >= 0;
                    object data = RunCommandAsync(call, payload, CancellationToken.None).GetAwaiter().GetResult();
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
            if (_smoke) { _form.Opacity = 0; _form.ShowInTaskbar = false; }
            _webView = new WebView2();
            _webView.Dock = DockStyle.Fill;
            _form.Controls.Add(_webView);
            _form.Shown += async (sender, e) => { await InitAsync(renderer); };
            _form.FormClosing += delegate { _closing = true; CancelRequests(); };
            _form.FormClosed += delegate { Application.Exit(); };
            var uiTimer = new System.Windows.Forms.Timer { Interval = 50 };
            uiTimer.Tick += delegate { _uiTicks++; };
            uiTimer.Start();

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
                _webView.CoreWebView2.NavigationStarting += delegate { _page++; CancelRequests(); };
                await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(BridgeScript());
                _webView.CoreWebView2.NavigationCompleted += async (sender, e) =>
                {
                    if (_smoke && !_smokeDone)
                    {
                        _smokeDone = true;
                        string js = "window.guild.rpc('projects').then(function(r){"
                            + "window.chrome.webview.postMessage({smoke:true,data:r});"
                            + "}).catch(function(e){window.chrome.webview.postMessage({smoke:false,error:String(e)});});";
                        if (_smokeCancel)
                            js = "var probe=window.guild.rpc('projects');setTimeout(function(){probe.cancel();},300);"
                                + "probe.then(function(){window.chrome.webview.postMessage({smoke:false,error:'expected cancellation'});})"
                                + ".catch(function(e){window.chrome.webview.postMessage({smoke:String(e).indexOf('unknown')>=0,error:String(e)});});";
                        if (_smokeRecovery)
                            js = "window.guild.rpc('recovery').then(function(r){state.recovery=r;renderRecovery();"
                                + "window.chrome.webview.postMessage({smoke:document.getElementById('recoveryRows').innerText.indexOf('进程归属需手工核对')>=0,data:r});"
                                + "}).catch(function(e){window.chrome.webview.postMessage({smoke:false,error:String(e)});});";
                        if (_smokeReview)
                            js = "(async function(){try{await loadAll();var c=state.candidates[0];"
                                + "var button=document.querySelector('[data-action=review]');if(!button)throw Error('review button missing');button.click();"
                                + "var running=false;for(var i=0;i<100;i++){await new Promise(r=>setTimeout(r,100));"
                                + "var s=await window.guild.rpc('reviews',{candidateId:c.candidate_id});"
                                + "if(s.reviews.some(r=>r.status==='running')){running=true;break;}}"
                                + "if(!running)throw Error('running state missing');"
                                + "if(" + (Environment.GetEnvironmentVariable("GUILD_REVIEW_OFFLINE_SCENARIO") == "timeout" ? "true" : "false") + ")await cancelAction('candidate:'+c.candidate_id);"
                                + "for(var i=0;i<150 && pendingActions.size;i++)await new Promise(r=>setTimeout(r,100));"
                                + "if(pendingActions.size)throw Error('request not settled');await loadAll();"
                                + "var text=document.getElementById('candidateCards').innerText;"
                                + "var s=state.candidates[0].reviewState;var last=s.reviews.slice(-1)[0];"
                                + "var interrupted=last.status==='running'&&state.recovery.executions.some(e=>e.role==='reviewer');"
                                + "window.chrome.webview.postMessage({smoke:text.indexOf('离线模拟')>=0&&(interrupted||last.report_path.length>0),data:s,interrupted:interrupted});"
                                + "}catch(e){window.chrome.webview.postMessage({smoke:false,error:String(e)});}})();";
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
                + "window.guild={rpc:function(cmd,payload){var id=++seq;var promise=new Promise(function(resolve,reject){"
                + "pending[id]={resolve:resolve,reject:reject};"
                + "window.chrome.webview.postMessage({id:id,cmd:cmd,payload:payload||{}});"
                + "});promise.cancel=function(){return window.guild.rpc('cancel_request',{requestId:id});};return promise;}};"
                + "window.chrome.webview.addEventListener('message',function(e){"
                + "var m=e.data;var p=pending[m.id];if(!p)return;delete pending[m.id];"
                + "if(m.ok){p.resolve(m.data);}else{p.reject(new Error(m.error||'rpc failed'));}"
                + "});"
                + "})();";
        }

        private static void CancelRequests()
        {
            foreach (var request in new List<CancellationTokenSource>(Requests.Values)) request.Cancel();
        }

        private static void PostResponse(Dictionary<string, object> response)
        {
            string encoded;
            try { encoded = Json.Serialize(response); }
            catch (Exception)
            {
                encoded = Json.Serialize(new {
                    id = response["id"], ok = false,
                    error = "Response could not be serialized or exceeded the size limit; result is unknown"
                });
            }
            _webView.CoreWebView2.PostWebMessageAsJson(encoded);
        }

        private static async void OnMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            try
            {
                Dictionary<string, object> msg =
                    Json.Deserialize<Dictionary<string, object>>(e.WebMessageAsJson);
                if (msg.ContainsKey("smoke"))
                {
                    msg["uiResponsive"] = _uiResponsive;
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
                int page = _page;
                string key = page + ":" + Convert.ToString(id);
                string mutation = "";
                CancellationTokenSource cancellation = null;
                bool ownsMutation = false;
                int ticksBefore = _uiTicks;
                try
                {
                    object data;
                    if (cmd == "cancel_request")
                    {
                        CancellationTokenSource active;
                        bool found = Requests.TryGetValue(page + ":" + GetString(payload, "requestId", true), out active);
                        if (found) active.Cancel();
                        data = new { requested = found, result = "unknown" };
                    }
                    else
                    {
                        mutation = (cmd == "start_run") ? "task:" + GetString(payload, "taskId", true)
                            : (cmd == "merge" || cmd == "approve" || cmd == "review_candidate") ? "candidate:" + GetString(payload, "candidateId", true) : "";
                        if (cmd == "reconcile_execution") mutation = "execution:" + GetString(payload, "executionId", true);
                        if (cmd == "reconcile_merge") mutation = "attempt:" + GetString(payload, "attemptId", true);
                        if (Requests.ContainsKey(key)) throw new Exception("request already active");
                        if (mutation != "" && !Mutations.Add(mutation)) throw new Exception("operation already active");
                        ownsMutation = mutation != "";
                        cancellation = new CancellationTokenSource();
                        Requests.Add(key, cancellation);
                        data = await Task.Run(() => RunCommandAsync(cmd, payload, cancellation.Token));
                    }
                    response["ok"] = true;
                    response["data"] = data;
                }
                catch (Exception ex)
                {
                    response["ok"] = false;
                    response["error"] = ex.Message;
                }
                finally
                {
                    if (cancellation != null) { Requests.Remove(key); cancellation.Dispose(); }
                    if (ownsMutation) Mutations.Remove(mutation);
                    if (_smoke && cmd == "projects") _uiResponsive = _uiTicks > ticksBefore;
                }
                if (!_closing && page == _page && !_webView.IsDisposed)
                    PostResponse(response);
            }
            catch (Exception ex)
            {
                Console.WriteLine("WEBVIEW2_MESSAGE_FAIL " + ex);
            }
        }

        private static async Task<object> RunCommandAsync(string cmd, Dictionary<string, object> payload, CancellationToken cancellation)
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
                string targetRef = GetString(payload, "targetRef", false);
                if (string.IsNullOrEmpty(targetRef)) targetRef = "HEAD";
                args.Add("--target-ref");
                args.Add(targetRef);
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
            else if (cmd == "review_candidate" || cmd == "reviews")
            {
                args.Add(cmd == "reviews" ? "reviews" : "review-candidate");
                args.Add("--candidate"); args.Add(GetString(payload, "candidateId", true));
                if (cmd == "review_candidate")
                {
                    string checkId = GetString(payload, "checkId", false);
                    string timeout = GetString(payload, "timeout", false);
                    if (!string.IsNullOrEmpty(checkId)) { args.Add("--check-id"); args.Add(checkId); }
                    if (!string.IsNullOrEmpty(timeout)) { args.Add("--timeout"); args.Add(timeout); }
                }
            }
            else if (cmd == "recovery")
            {
                args.Add("recovery-report");
                string project = GetString(payload, "projectId", false);
                if (!string.IsNullOrEmpty(project)) { args.Add("--project"); args.Add(project); }
            }
            else if (cmd == "reconcile_execution")
            {
                args.Add("reconcile-execution");
                args.Add("--execution"); args.Add(GetString(payload, "executionId", true));
                args.Add("--generation"); args.Add(GetString(payload, "generation", true));
                args.Add("--reason"); args.Add(GetString(payload, "reason", true));
                if (payload.ContainsKey("confirmLocalStopped") && object.Equals(payload["confirmLocalStopped"], true))
                    args.Add("--confirm-local-stopped");
            }
            else if (cmd == "reconcile_merge")
            {
                args.Add("reconcile-merge");
                args.Add("--attempt"); args.Add(GetString(payload, "attemptId", true));
                args.Add("--action"); args.Add(GetString(payload, "action", true));
                args.Add("--result-sha"); args.Add(GetString(payload, "resultSha", true));
                string reason = GetString(payload, "reason", false);
                if (!string.IsNullOrEmpty(reason)) { args.Add("--reason"); args.Add(reason); }
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

            string output = await RunPythonCliAsync(args, cancellation).ConfigureAwait(false);
            return await Task.Run(() => new JavaScriptSerializer().DeserializeObject(output)).ConfigureAwait(false);
        }

        private static Task<string> RunPythonCliAsync(List<string> args, CancellationToken cancellation)
        {
            string python = Environment.GetEnvironmentVariable("PYTHON");
            List<string> full = new List<string>();
            if (string.IsNullOrEmpty(python)) { python = "py.exe"; full.Add("-3"); }
            string db = Environment.GetEnvironmentVariable("GUILD_DB");
            if (!string.IsNullOrEmpty(db))
            {
                db = Path.GetFullPath(Path.IsPathRooted(db) ? db : Path.Combine(_p0Root, db));
                full.Add("--db");
                full.Add(db);
            }
            full.AddRange(args);

            full.Insert(string.IsNullOrEmpty(Environment.GetEnvironmentVariable("PYTHON")) ? 1 : 0,
                Path.Combine(_p0Root, "worker_cli.py"));
            return AsyncProcessRunner.RunAsync(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "AgentGuildJobHost.exe"),
                python, full.ToArray(), _p0Root, cancellation, TimeSpan.FromMinutes(30),
                Path.Combine(string.IsNullOrEmpty(db) ? Path.Combine(_p0Root, "data") : Path.GetDirectoryName(Path.GetFullPath(db)), "request-receipts"));
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

    }
}
