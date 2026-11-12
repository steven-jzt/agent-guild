# Build the WebView2 shell with the .NET Framework csc compiler.
# Requires: Windows .NET Framework 4.8, network for the WebView2 SDK NuGet package.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$sdkRoot = Join-Path $root '.tmp\webview2sdk'
$pkgDir = Join-Path $sdkRoot 'pkg'
$nupkg = Join-Path $sdkRoot 'webview2.nupkg'
$zip = Join-Path $sdkRoot 'webview2.zip'
$out = Join-Path $here 'bin'
$version = '1.0.4314.35'

New-Item -ItemType Directory -Force -Path $sdkRoot, $out | Out-Null
$refDir = Join-Path $pkgDir 'lib\net462'
if (-not (Test-Path (Join-Path $refDir 'Microsoft.Web.WebView2.WinForms.dll')) -and
    (Test-Path (Join-Path $out 'Microsoft.Web.WebView2.WinForms.dll'))) {
    $refDir = $out # Reuse checked-in SDK assemblies for an offline build.
}
if (-not (Test-Path (Join-Path $refDir 'Microsoft.Web.WebView2.WinForms.dll'))) {
    Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$version/microsoft.web.webview2.$version.nupkg" -OutFile $nupkg
    Copy-Item $nupkg $zip -Force
    Expand-Archive -Path $zip -DestinationPath $pkgDir -Force
}

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$refs = @(
    '/r:System.dll', '/r:System.Core.dll', '/r:System.Windows.Forms.dll',
    '/r:System.Drawing.dll', '/r:System.Web.Extensions.dll',
    "/r:$refDir\Microsoft.Web.WebView2.Core.dll",
    "/r:$refDir\Microsoft.Web.WebView2.WinForms.dll"
)

$sources = @((Join-Path $here 'Program.cs'), (Join-Path $here 'AsyncProcessRunner.cs'))
& $csc /nologo /target:exe /platform:x64 /out:"$out\AgentGuildJobHost.exe" (Join-Path $root '..\agent-guild\src\transport\JobHost.cs')
if ($LASTEXITCODE -ne 0) { throw "csc JobHost failed: $LASTEXITCODE" }
& $csc /nologo /target:winexe /platform:x64 /out:"$out\AgentGuildWebView2.exe" @refs @sources
if ($LASTEXITCODE -ne 0) { throw "csc GUI failed: $LASTEXITCODE" }

& $csc /nologo /target:exe /platform:x64 /out:"$out\AgentGuildWebView2Cli.exe" @refs @sources
if ($LASTEXITCODE -ne 0) { throw "csc CLI failed: $LASTEXITCODE" }

if ($refDir -ne $out) {
    Copy-Item "$refDir\Microsoft.Web.WebView2.Core.dll" $out -Force
    Copy-Item "$refDir\Microsoft.Web.WebView2.WinForms.dll" $out -Force
    Copy-Item "$pkgDir\runtimes\win-x64\native\WebView2Loader.dll" $out -Force
}
Write-Output "built: $out\AgentGuildWebView2.exe (GUI)"
Write-Output "built: $out\AgentGuildWebView2Cli.exe (CLI smoke)"
