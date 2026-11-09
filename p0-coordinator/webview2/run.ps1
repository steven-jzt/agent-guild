$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $here 'bin\AgentGuildWebView2.exe') @args
