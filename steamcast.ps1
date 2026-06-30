<#
.SYNOPSIS
  SteamCast — Prepare and broadcast multiple game trailers to Steam store pages.
.DESCRIPTION
  Two-phase tool:
    PREP  — Convert video files to Steam RTMP spec and concatenate by game.
    CAST  — Configure RTMP keys, toggle streams on/off, monitor health.
  All files stored locally. No telemetry, no cloud, no internet (except FFmpeg download on first run).
  Supports: NVIDIA NVENC, Intel QSV, AMD AMF, software fallback (libx264).
#>

#region ─── CONFIG & PATHS ─────────────────────────────────────────────

$Script:Version    = "1.0.0-beta"
$Script:RootDir    = Split-Path -Parent $PSCommandPath
$Script:InputDir   = Join-Path $Script:RootDir "input"
$Script:OutputDir  = Join-Path $Script:RootDir "output"
$Script:FFmpegDir  = Join-Path $Script:RootDir "ffmpeg"
$Script:FFmpegExe  = Join-Path $Script:FFmpegDir "ffmpeg.exe"
$Script:ConfigPath = Join-Path $Script:RootDir "config.json"
$Script:LogDir     = Join-Path $Script:RootDir "logs"

# Steam broadcast spec (default)
$Script:VideoCodec     = "h264_nvenc"
$Script:VideoProfile   = "high"
$Script:VideoLevel     = "4.1"
$Script:VideoBitrate   = "7000k"
$Script:VideoFPS       = 30
$Script:VideoWidth     = 1920
$Script:VideoHeight    = 1080
$Script:KeyframeInt    = 60  # 2s at 30fps
$Script:AudioCodec     = "aac"
$Script:AudioBitrate   = "128k"

# FFmpeg download
$Script:FFmpegUrl      = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$Script:FFmpegZipPath  = Join-Path $Script:FFmpegDir "ffmpeg.zip"
$Script:MaxRetries     = 5
$Script:RetryDelaySec  = 3

# Version check URL (raw GitHub)
$Script:VersionCheckUrl = "https://raw.githubusercontent.com/underagum/steamcast/main/version.txt"

# Cast tracking
$Script:ActiveStreams  = @{}  # game_name -> @{ PID; start_time; log_file }

# Job object handle (kills children on terminal close)
$Script:JobHandle      = [System.IntPtr]::Zero

# Console colours
$Script:CCyan    = "Cyan"
$Script:CGreen   = "Green"
$Script:CYellow  = "Yellow"
$Script:CRed     = "Red"
$Script:CWhite   = "White"
$Script:CMagenta = "Magenta"

#endregion

#region ─── JOB OBJECT (orphan prevention) ────────────────────────────

# P/Invoke to create a Windows Job Object — when the parent dies,
# Windows kills every process assigned to the job.
Add-Type @"
using System;
using System.Runtime.InteropServices;

public class WinJob {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool TerminateJobObject(IntPtr hJob, uint uExitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr hObject);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr GetCurrentProcess();
}
"@

function Initialize-JobObject {
    $Script:JobHandle = [WinJob]::CreateJobObject([System.IntPtr]::Zero, "SteamCast_$PID")
    $currentProc = [WinJob]::GetCurrentProcess()
    $assigned = [WinJob]::AssignProcessToJobObject($Script:JobHandle, $currentProc)
    if (-not $assigned) {
        Write-Host "[!] Could not secure process group — orphan ffmpeg may survive terminal close." -ForegroundColor Yellow
    } else {
        Write-Host "[+] Process group secured — orphaned ffmpeg will be killed on exit." -ForegroundColor $Script:CGreen
    }
}

function Cleanup-JobObject {
    if ($Script:JobHandle -ne [System.IntPtr]::Zero) {
        Write-Host "[*] Cleaning up child processes..." -ForegroundColor $Script:CYellow
        [WinJob]::TerminateJobObject($Script:JobHandle, 1) | Out-Null
        [WinJob]::CloseHandle($Script:JobHandle) | Out-Null
        $Script:JobHandle = [System.IntPtr]::Zero
        Write-Host "[√] Done." -ForegroundColor $Script:CGreen
    }
}

# Register exit handler to clean up job object (best-effort backup; try/finally blocks are the primary mechanism)
$Script:ExitHandler = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action {
    Cleanup-JobObject
}

# Also handle CTRL+C gracefully
[Console]::TreatControlCAsInput = $false

#endregion

#region ─── VERSION CHECK ─────────────────────────────────────────────

function Check-NewerVersion {
    try {
        $remoteVersion = Invoke-WebRequest -Uri $Script:VersionCheckUrl -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        $remote = ($remoteVersion.Content.Trim())
        if ($remote -and $remote -ne $Script:Version) {
            Write-Host ""
            Write-Host "[!] A newer version of SteamCast ($remote) is available!" -ForegroundColor $Script:CYellow
            Write-Host "[!] Download: https://github.com/underagum/steamcast/releases" -ForegroundColor $Script:CYellow
            Write-Host ""
        }
    } catch {
        # Offline or unreachable — silently ignore
    }
}

#endregion

#region ─── UTILITY ────────────────────────────────────────────────────

function Write-Banner {
    Clear-Host
    Write-Host @"
  ╔══════════════════════════════════════╗
  ║          STEAMCAST v$($Script:Version)            ║
  ║   Steam broadcast video prep & cast  ║
  ╚══════════════════════════════════════╝
"@ -ForegroundColor $Script:CMagenta
}

function Show-Error { param([string]$Msg) Write-Host "[x] $Msg" -ForegroundColor $Script:CRed }
function Show-Info  { param([string]$Msg) Write-Host "[i] $Msg" -ForegroundColor $Script:CCyan }
function Show-Ok    { param([string]$Msg) Write-Host "[√] $Msg" -ForegroundColor $Script:CGreen }
function Show-Warn  { param([string]$Msg) Write-Host "[!] $Msg" -ForegroundColor $Script:CYellow }
function Show-Step  { param([string]$Msg) Write-Host "[*] $Msg" -ForegroundColor $Script:CWhite }

function Pause-And-Continue {
    Write-Host "`nPress Enter to continue..." -NoNewline
    $null = [Console]::ReadLine()
}

function Get-UserInput {
    param([string]$Prompt, [string]$Default = "")
    $prompt = "[?] $Prompt"
    if ($Default) { $prompt += " (default: $Default)" }
    Write-Host "$prompt`: " -NoNewline -ForegroundColor $Script:CCyan
    $val = [Console]::ReadLine()
    if ([string]::IsNullOrWhiteSpace($val)) { return $Default }
    return $val.Trim()
}

function Get-YesNo {
    param([string]$Prompt)
    while ($true) {
        Write-Host "[?] $Prompt (Y/N): " -NoNewline -ForegroundColor $Script:CCyan
        $key = [Console]::ReadKey($true)
        Write-Host $key.KeyChar
        if ($key.Key -eq [ConsoleKey]::Y) { return $true }
        if ($key.Key -eq [ConsoleKey]::N) { return $false }
        if ($key.Key -eq [ConsoleKey]::Enter) { return $true }    # Enter = default Yes
        if ($key.Key -eq [ConsoleKey]::Escape) { return $false }  # Escape = default No
    }
}

#endregion

#region ─── CONFIG ─────────────────────────────────────────────────────

function Get-Config {
    if (Test-Path $Script:ConfigPath) {
        try {
            return Get-Content $Script:ConfigPath -Raw | ConvertFrom-Json
        } catch {
            Show-Warn "Config file corrupted. Starting fresh."
        }
    }
    return New-ConfigObject
}

function New-ConfigObject {
    return [PSCustomObject]@{
        version = $Script:Version
        games   = @{}
    }
}

function Save-Config {
    param($Config)
    $Config | ConvertTo-Json -Depth 10 | Set-Content $Script:ConfigPath -Encoding UTF8
}

function Get-GameNamesFromConfig {
    $cfg = Get-Config
    return $cfg.games.PSObject.Properties.Name
}

function Get-RTMPKey {
    param([string]$GameName)
    $cfg = Get-Config
    return $cfg.games."$GameName".rtmp_key
}

function Set-RTMPKey {
    param([string]$GameName, [string]$Key)
    $cfg = Get-Config
    if (-not $cfg.games."$GameName") {
        $cfg.games | Add-Member -NotePropertyName $GameName -NotePropertyValue ([PSCustomObject]@{ rtmp_key = $Key; active = $false })
    } else {
        $cfg.games."$GameName".rtmp_key = $Key
    }
    Save-Config $cfg
}

function Get-GameActive {
    param([string]$GameName)
    $cfg = Get-Config
    if (-not $cfg.games."$GameName") { return $false }
    return $cfg.games."$GameName".active -eq $true
}

function Set-GameActive {
    param([string]$GameName, [bool]$Active)
    $cfg = Get-Config
    if (-not $cfg.games."$GameName") {
        $cfg.games | Add-Member -NotePropertyName $GameName -NotePropertyValue ([PSCustomObject]@{ rtmp_key = ""; active = $Active })
    } else {
        $cfg.games."$GameName".active = $Active
    }
    Save-Config $cfg
}

#endregion

#region ─── FFMPEG ─────────────────────────────────────────────────────

function Test-FFmpegAvailable {
    if (Test-Path $Script:FFmpegExe) {
        $Script:FFmpegPath = $Script:FFmpegExe
        return $true
    }
    # Check PATH
    $pathExe = Get-Command "ffmpeg.exe" -ErrorAction SilentlyContinue
    if ($pathExe) {
        $Script:FFmpegPath = $pathExe.Source
        return $true
    }
    return $false
}

function Invoke-FFmpegDownload {
    Show-Step "FFmpeg not found. Downloading portable build from gyan.dev (~55 MB)..."
    
    if (-not (Test-Path $Script:FFmpegDir)) { New-Item -ItemType Directory -Path $Script:FFmpegDir -Force | Out-Null }
    
    $attempt = 0
    $downloaded = $false
    while ($attempt -lt $Script:MaxRetries -and -not $downloaded) {
        $attempt++
        try {
            Show-Step "Download attempt $attempt/$($Script:MaxRetries)..."
            Write-Host "Downloading FFmpeg (~55MB)..." -NoNewline
            Invoke-WebRequest -Uri $Script:FFmpegUrl -OutFile $Script:FFmpegZipPath -TimeoutSec 60
            Write-Host " Done!"
            $downloaded = $true
        } catch {
            Show-Warn "Download failed: $($_.Exception.Message)"
            if ($attempt -lt $Script:MaxRetries) {
                Show-Step "Retrying in $($Script:RetryDelaySec)s..."
                Start-Sleep $Script:RetryDelaySec
            }
        }
    }
    
    if (-not $downloaded) {
        Show-Error "Could not download FFmpeg after $($Script:MaxRetries) attempts."
        Show-Info "Please download manually from: $($Script:FFmpegUrl)"
        Show-Info "Extract ffmpeg.exe into: $($Script:FFmpegDir)"
        Pause-And-Continue
        return $false
    }
    
    Show-Step "Extracting..."
    try {
        Expand-Archive -Path $Script:FFmpegZipPath -DestinationPath $Script:FFmpegDir -Force
        
        # Find ffmpeg.exe in the extracted directory structure
        $found = Get-ChildItem -Path $Script:FFmpegDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        if ($found) {
            Copy-Item $found.FullName $Script:FFmpegExe -Force
            # Cleanup extraction
            Get-ChildItem -Path $Script:FFmpegDir -Directory | Remove-Item -Recurse -Force
            Remove-Item $Script:FFmpegZipPath -Force
            Show-Ok "FFmpeg ready at $($Script:FFmpegExe)"
            return $true
        } else {
            throw "Could not locate ffmpeg.exe in extracted archive."
        }
    } catch {
        Show-Error "Extraction failed: $($_.Exception.Message)"
        Show-Info "Please extract manually from $($Script:FFmpegZipPath)"
        return $false
    }
}

function Test-HardwareEncoder {
    param([string]$EncoderName)
    $encoderList = & $Script:FFmpegPath -hide_banner -encoders 2>&1 | Out-String
    return $encoderList -match $EncoderName
}

function Get-EncoderSettings {
    # Priority 1: NVIDIA NVENC
    if (Test-HardwareEncoder -EncoderName "h264_nvenc") {
        Show-Info "NVIDIA NVENC detected — using hardware encoding."
        return @{
            codec      = "h264_nvenc"
            preset     = "p7"
            cbr_flags  = "-rc cbr"
        }
    }
    # Priority 2: Intel QSV
    if (Test-HardwareEncoder -EncoderName "h264_qsv") {
        Show-Info "Intel QSV detected — using hardware encoding."
        return @{
            codec      = "h264_qsv"
            preset     = "veryfast"
            cbr_flags  = ""  # QSV: only -b:v -maxrate -bufsize, NO -rc cbr
        }
    }
    # Priority 3: AMD AMF (only available in FULL ffmpeg build, not ESSENTIALS)
    if (Test-HardwareEncoder -EncoderName "h264_amf") {
        Show-Info "AMD AMF detected — using hardware encoding."
        return @{
            codec      = "h264_amf"
            preset     = "quality"
            cbr_flags  = "-rc cbr"
        }
    }
    # Fallback: libx264 software
    Show-Warn "No compatible hardware encoder detected. Using software encoding (libx264) — this will be slower."
    return @{
        codec      = "libx264"
        preset     = "slow"
        cbr_flags  = ""
    }
}

function Invoke-FFmpegConvert {
    param(
        [string]$InputFile,
        [string]$OutputFile,
        [switch]$ShowOutput
    )
    
    $enc = Get-EncoderSettings
    
    # Build argument array — NEVER build a single string command
    $ffArgs = @(
        "-y",
        "-i", $InputFile,
        "-c:v", $enc.codec,
        "-preset", $enc.preset,
        "-profile:v", $Script:VideoProfile,
        "-level:v", $Script:VideoLevel,
        "-b:v", $Script:VideoBitrate,
        "-maxrate", $Script:VideoBitrate,
        "-bufsize", [math]::Round([int]($Script:VideoBitrate -replace 'k','') * 2).ToString() + "k",
        "-g", $Script:KeyframeInt,
        "-keyint_min", $Script:KeyframeInt,
        "-sc_threshold", "0",
        "-r", $Script:VideoFPS,
        "-s", "$($Script:VideoWidth)x$($Script:VideoHeight)",
        "-c:a", $Script:AudioCodec,
        "-b:a", $Script:AudioBitrate,
        "-movflags", "+faststart",
        $OutputFile
    )
    
    # Insert encoder-specific CBR flags
    if ($enc.cbr_flags) {
        $ffArgs = $ffArgs[0..3] + @($enc.cbr_flags -split ' ') + $ffArgs[4..($ffArgs.Length-1)]
    }
    
    if ($ShowOutput) {
        $argString = $ffArgs -join ' '
        Show-Step "Running: $($Script:FFmpegPath) $argString"
    }
    
    if ($ShowOutput) {
        & $Script:FFmpegPath @ffArgs
    } else {
        $null = & $Script:FFmpegPath @ffArgs 2>&1
    }
    
    return $LASTEXITCODE -eq 0 -and (Test-Path $OutputFile)
}

function Invoke-FFmpegConcat {
    param(
        [string]$PlaylistFile,
        [string]$OutputFile,
        [switch]$ShowOutput
    )
    
    $enc = Get-EncoderSettings
    
    # Build argument array — NEVER build a single string command
    $ffArgs = @(
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", $PlaylistFile,
        "-c:v", $enc.codec,
        "-preset", $enc.preset,
        "-profile:v", $Script:VideoProfile,
        "-level:v", $Script:VideoLevel,
        "-b:v", $Script:VideoBitrate,
        "-maxrate", $Script:VideoBitrate,
        "-bufsize", [math]::Round([int]($Script:VideoBitrate -replace 'k','') * 2).ToString() + "k",
        "-g", $Script:KeyframeInt,
        "-keyint_min", $Script:KeyframeInt,
        "-sc_threshold", "0",
        "-r", $Script:VideoFPS,
        "-s", "$($Script:VideoWidth)x$($Script:VideoHeight)",
        "-c:a", $Script:AudioCodec,
        "-b:a", $Script:AudioBitrate,
        "-movflags", "+faststart",
        $OutputFile
    )
    
    # Insert encoder-specific CBR flags
    if ($enc.cbr_flags) {
        $ffArgs = $ffArgs[0..3] + @($enc.cbr_flags -split ' ') + $ffArgs[4..($ffArgs.Length-1)]
    }
    
    Show-Step "Concatenating..."
    
    if ($ShowOutput) {
        & $Script:FFmpegPath @ffArgs
    } else {
        $null = & $Script:FFmpegPath @ffArgs 2>&1
    }
    
    return $LASTEXITCODE -eq 0 -and (Test-Path $OutputFile)
}

function Get-FileDuration {
    param([string]$FilePath)
    $result = & $Script:FFmpegPath -i "$FilePath" -hide_banner 2>&1 | Select-String "Duration:"
    if ($result) {
        $match = [regex]::Match($result, "Duration: (\d+:\d+:\d+\.\d+)")
        if ($match.Success) { return $match.Groups[1].Value }
    }
    return "??:??:??"
}

function Get-StreamBitrate {
    param([string]$LogFile)
    if (-not (Test-Path $LogFile)) { return "---" }
    $lastLine = Get-Content $LogFile -Tail 5 | Select-String "bitrate=" | Select-Object -Last 1
    if ($lastLine) {
        $match = [regex]::Match($lastLine, "bitrate=\s*([\d.]+)\s*kbits/s")
        if ($match.Success) { return "$($match.Groups[1].Value) kbps" }
    }
    return "---"
}

function Get-StreamTime {
    param([string]$LogFile)
    if (-not (Test-Path $LogFile)) { return "--:--:--" }
    $lastLine = Get-Content $LogFile -Tail 5 | Select-String "time=" | Select-Object -Last 1
    if ($lastLine) {
        $match = [regex]::Match($lastLine, "time=(\d+:\d+:\d+\.\d+)")
        if ($match.Success) { return $match.Groups[1].Value }
    }
    return "--:--:--"
}

#endregion

#region ─── PREP PHASE ─────────────────────────────────────────────────

function Get-GameNameFromFile {
    param([string]$FileName)
    $base = [System.IO.Path]::GetFileNameWithoutExtension($FileName)
    $match = [regex]::Match($base, "^(.+)_(\d+)$")
    if ($match.Success) {
        return @{
            gameName  = $match.Groups[1].Value.Trim()
            partNum   = [int]$match.Groups[2].Value
            isPart    = $true
        }
    }
    return @{
        gameName  = $base.Trim()
        partNum   = 0
        isPart    = $false
    }
}

function Invoke-Prep {
    Write-Banner
    Write-Host "=== PREP: Video Preparation ===" -ForegroundColor $Script:CYellow
    Write-Host ""
    
    # Ensure directories
    if (-not (Test-Path $Script:InputDir)) { New-Item -ItemType Directory -Path $Script:InputDir -Force | Out-Null }
    if (-not (Test-Path $Script:OutputDir)) { New-Item -ItemType Directory -Path $Script:OutputDir -Force | Out-Null }
    
    # Check for FFmpeg
    if (-not (Test-FFmpegAvailable)) {
        $ok = Invoke-FFmpegDownload
        if (-not $ok) { return }
    }
    
    # Step 1: Ask user to put videos
    Write-Host "Step 1: Copy your video files into:" -ForegroundColor $Script:CWhite
    Write-Host "       $($Script:InputDir)" -ForegroundColor $Script:CCyan
    Write-Host ""
    Write-Host "Naming guide:" -ForegroundColor $Script:CYellow
    Write-Host "  Single video:     gamename.mp4 (e.g. `"dreadout 2.mp4`")" -ForegroundColor $Script:CWhite
    Write-Host "  Multiple videos:  gamename_1.mp4, gamename_2.mp4 (e.g. `"dreadout 2_1.mp4`")" -ForegroundColor $Script:CWhite
    Write-Host ""
    
    $ready = Get-YesNo "Have you placed all video files in the input folder?"
    if (-not $ready) { Write-Host "Come back when ready!" -ForegroundColor $Script:CYellow; return }
    
    # Step 2: Scan for videos (including subdirectories)
    Show-Step "Scanning input folder (including subdirectories)..."
    $videoExtensions = @("*.mp4", "*.avi", "*.mov", "*.mkv", "*.wmv", "*.webm", "*.flv", "*.m4v")
    $videoFiles = @()
    foreach ($ext in $videoExtensions) {
        $videoFiles += Get-ChildItem -Path $Script:InputDir -Filter $ext -Recurse
    }
    
    if ($videoFiles.Count -eq 0) {
        Show-Error "No video files found in input folder."
        $extList = $videoExtensions -join ', '
        Show-Info "Supported formats: $extList"
        Pause-And-Continue
        return
    }
    
    Write-Host "`nFound $($videoFiles.Count) video file(s):" -ForegroundColor $Script:CGreen
    foreach ($f in $videoFiles) {
        $dur = Get-FileDuration $f.FullName
        $size = [math]::Round($f.Length / 1MB, 1)
        Write-Host "  $($f.Name)  ($dur, $size MB)" -ForegroundColor $Script:CWhite
    }
    
    # Step 3: Group by game name
    $gameGroups = @{}
    foreach ($f in $videoFiles) {
        $info = Get-GameNameFromFile $f.Name
        $gameName = $info.gameName
        if (-not $gameGroups.ContainsKey($gameName)) {
            $gameGroups[$gameName] = @()
        }
        $gameGroups[$gameName] += $f
    }
    
    Write-Host "`nGrouped by game:" -ForegroundColor $Script:CYellow
    foreach ($gName in $gameGroups.Keys | Sort-Object) {
        $files = $gameGroups[$gName]
        $action = if ($files.Count -gt 1) { "convert + concat ($($files.Count) files)" } else { "convert only" }
        $outPath = Join-Path $Script:OutputDir "$gName.mp4"
        $exists = Test-Path $outPath
        $existsStr = if ($exists) { " [EXISTS]" } else { "" }
        Write-Host "  [$gName] $action$existsStr" -ForegroundColor $Script:CWhite
    }
    
    Write-Host ""
    $proceed = Get-YesNo "Proceed with prep?"
    if (-not $proceed) { return }
    
    # Step 4: Process each game group
    foreach ($gName in $gameGroups.Keys | Sort-Object) {
        $files = $gameGroups[$gName] | Sort-Object Name
        # Sanitize game name for filesystem safety
        $safeName = $gName -replace '[<>:"/\\|?*]', '_'
        if ($safeName.Length -gt 80) { $safeName = $safeName.Substring(0, 80) }
        $outPath = Join-Path $Script:OutputDir "$safeName.mp4"
        
        # Check if output exists
        if (Test-Path $outPath) {
            $overwrite = Get-YesNo "`"$gName.mp4`" already exists. Overwrite?"
            if (-not $overwrite) {
                Show-Step "Skipping $gName"
                continue
            }
        }
        
        if ($files.Count -eq 1) {
            # Single file — just convert
            Show-Step "Converting $gName..."
            $ok = Invoke-FFmpegConvert -InputFile $files[0].FullName -OutputFile $outPath
            if ($ok) {
                Show-Ok "$gName converted successfully"
            } else {
                Show-Error "Failed to convert $gName"
            }
        } else {
            # Multiple files — convert each, then concat
            $tempDir = Join-Path $Script:InputDir ".temp_$([System.Guid]::NewGuid().ToString().Substring(0,8))"
            New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
            
            $convertedFiles = @()
            $allOk = $true
            
            foreach ($f in $files) {
                $tempOut = Join-Path $tempDir "$($f.BaseName)_steam.mp4"
                Show-Step "Converting $($f.Name)..."
                $ok = Invoke-FFmpegConvert -InputFile $f.FullName -OutputFile $tempOut
                if (-not $ok) {
                    Show-Error "Failed to convert $($f.Name)"
                    $allOk = $false
                    break
                }
                $convertedFiles += $tempOut
            }
            
            if ($allOk) {
                # Create playlist for concat — escape single quotes for filenames with special chars
                $playlistPath = Join-Path $tempDir "playlist.txt"
                $convertedFiles | ForEach-Object {
                    $escaped = $_.Replace('\','/').Replace("'","'\\''")
                    "file '$escaped'"
                } | Set-Content $playlistPath -Encoding ASCII
                
                # Concat
                Show-Step "Concatenating $($convertedFiles.Count) files for $gName..."
                $ok = Invoke-FFmpegConcat -PlaylistFile $playlistPath -OutputFile $outPath
                if ($ok) {
                    Show-Ok "$gName ready: $outPath"
                } else {
                    Show-Error "Failed to concatenate $gName"
                }
            }
            
            # Cleanup temp
            if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
        }
    }
    
    # Report results
    Write-Host "`n=== Prep Complete ===" -ForegroundColor $Script:CGreen
    $outputFiles = Get-ChildItem -Path $Script:OutputDir -Filter "*.mp4"
    if ($outputFiles.Count -gt 0) {
        Show-Ok "Prepared $($outputFiles.Count) game file(s):"
        foreach ($f in $outputFiles | Sort-Object Name) {
            $size = [math]::Round($f.Length / 1MB, 1)
            Show-Info "  $($f.Name) ($size MB)"
        }
    }
    
    Pause-And-Continue
}

#endregion

#region ─── CAST PHASE ─────────────────────────────────────────────────

function Invoke-CastSetup {
    Write-Banner
    Write-Host "=== CAST SETUP: RTMP Key Configuration ===" -ForegroundColor $Script:CYellow
    Write-Host ""
    Show-Info "RTMP keys are stored locally in: $($Script:ConfigPath)"
    Show-Info "No data is sent anywhere — this file stays on your machine."
    Write-Host ""
    
    # Find available game videos
    if (-not (Test-Path $Script:OutputDir)) { New-Item -ItemType Directory -Path $Script:OutputDir -Force | Out-Null }
    $availableVideos = Get-ChildItem -Path $Script:OutputDir -Filter "*.mp4"
    
    $cfg = Get-Config
    $existingGames = @($cfg.games.PSObject.Properties.Name)
    
    if ($existingGames.Count -gt 0) {
        Write-Host "Games already configured:" -ForegroundColor $Script:CYellow
        foreach ($g in $existingGames | Sort-Object) {
            $key = Get-RTMPKey $g
            $masked = if ($key) { "$($key.Substring(0, [Math]::Min(8, $key.Length)))..." } else { "(no key)" }
            $safeG = $g -replace '[<>:"/\\|?*]', '_'
            if ($safeG.Length -gt 80) { $safeG = $safeG.Substring(0, 80) }
            $hasVideo = Test-Path (Join-Path $Script:OutputDir "$safeG.mp4")
            $videoStatus = if ($hasVideo) { "✓" } else { "⚠ no video" }
            Write-Host "  $g [$masked] $videoStatus" -ForegroundColor $Script:CWhite
        }
    } else {
        Show-Info "No games configured yet."
    }
    
    if ($availableVideos.Count -gt 0) {
        Write-Host "`nAvailable game videos in output folder:" -ForegroundColor $Script:CYellow
        foreach ($f in $availableVideos | Sort-Object Name) {
            $gameName = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
            $hasKey = [string]::IsNullOrWhiteSpace((Get-RTMPKey $gameName)) -eq $false
            $status = if ($hasKey) { "✓ key set" } else { "⚠ no key" }
            Write-Host "  $($f.Name)  — $status" -ForegroundColor $Script:CWhite
        }
    }
    
    # Setup wizard
    Write-Host "`n--- Add / Update Keys ---" -ForegroundColor $Script:CYellow
    $adding = $true
    while ($adding) {
        # Show suggestions
        $suggestions = @()
        if ($availableVideos) {
            foreach ($f in $availableVideos | Sort-Object Name) {
                $gn = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
                $hk = [string]::IsNullOrWhiteSpace((Get-RTMPKey $gn)) -eq $false
                if (-not $hk) { $suggestions += $gn }
            }
        }
        if ($suggestions.Count -gt 0) {
            $suggestionList = $suggestions -join ', '
            Write-Host "Games needing keys: $suggestionList" -ForegroundColor $Script:CYellow
        }
        
        $gameName = Get-UserInput "Enter game name (or leave empty to finish)"
        if ([string]::IsNullOrWhiteSpace($gameName)) { break }
        
        $currentKey = Get-RTMPKey $gameName
        if ($currentKey) {
            Show-Info "Current key: $($currentKey.Substring(0, [Math]::Min(8, $currentKey.Length)))..."
            $change = Get-YesNo "Change it?"
            if (-not $change) { continue }
        }
        
        $key = Get-UserInput "Enter RTMP key for '$gameName'" -Default $currentKey
        if (-not [string]::IsNullOrWhiteSpace($key)) {
            Set-RTMPKey -GameName $gameName -Key $key
            Show-Ok "Key saved for '$gameName'"
        }
    }
    
    Show-Ok "Setup complete."
    Pause-And-Continue
}

function Invoke-Cast {
    Write-Banner
    Write-Host "=== CAST: Stream Toggle ===" -ForegroundColor $Script:CYellow
    Write-Host ""
    
    # Ensure FFmpeg
    if (-not (Test-FFmpegAvailable)) {
        $ok = Invoke-FFmpegDownload
        if (-not $ok) { return }
    }
    
    # Load config
    $cfg = Get-Config
    $configGames = @($cfg.games.PSObject.Properties.Name)
    
    if ($configGames.Count -eq 0) {
        Show-Warn "No games configured yet."
        $goSetup = Get-YesNo "Go to setup?"
        if ($goSetup) { Invoke-CastSetup; Invoke-Cast; return }
        return
    }
    
    # Scan output folder
    if (-not (Test-Path $Script:OutputDir)) { New-Item -ItemType Directory -Path $Script:OutputDir -Force | Out-Null }
    $availableVideos = @{}
    Get-ChildItem -Path $Script:OutputDir -Filter "*.mp4" | ForEach-Object {
        $availableVideos[([System.IO.Path]::GetFileNameWithoutExtension($_.Name)).ToLower()] = $_.FullName
    }
    
    # Run the toggle menu loop
    $exitCast = $false
    while (-not $exitCast) {
        Write-Banner
        Write-Host "=== CAST: Select Games ===" -ForegroundColor $Script:CYellow
        Write-Host ""
        
        $menuItems = @()
        $i = 1
        foreach ($gName in ($configGames | Sort-Object)) {
            $isActive = Get-GameActive $gName
            $hasVideo = $availableVideos.ContainsKey($gName.ToLower())
            $rtmpKey = Get-RTMPKey $gName
            $hasKey = -not [string]::IsNullOrWhiteSpace($rtmpKey)
            
            $toggleStr = if ($isActive) { "ON" } else { "OFF" }
            $statusStr = if ($hasVideo) { 
                if ($hasKey) { "✓ ready" } else { "⚠ no key" }
            } else {
                "⚠ no video"
            }
            
            Write-Host "[$i] $gName  [$toggleStr]  $statusStr" -ForegroundColor $Script:CWhite
            $menuItems += @{ index = $i; game = $gName; active = $isActive; hasVideo = $hasVideo; hasKey = $hasKey }
            $i++
        }
        
        Write-Host ""
        Write-Host "[T] Toggle ALL" -ForegroundColor $Script:CCyan
        Write-Host "[A] Add/Edit keys (Setup)" -ForegroundColor $Script:CCyan
        Write-Host "[P] Go to Prep" -ForegroundColor $Script:CCyan
        Write-Host "[S] Start broadcasting" -ForegroundColor $Script:CGreen
        Write-Host "[Q] Back to main menu" -ForegroundColor $Script:CRed
        Write-Host ""
        Write-Host "Enter number to toggle, or command: " -NoNewline -ForegroundColor $Script:CCyan
        
        $input = "$([Console]::ReadLine())".Trim().ToLower()
        
        if ($input -eq "q") { $exitCast = $true; break }
        elseif ($input -eq "t") {
            $anyOn = ($menuItems | Where-Object { $_.active }).Count -gt 0
            $newState = -not $anyOn
            foreach ($item in $menuItems) { 
                Set-GameActive -GameName $item.game -Active $newState
            }
            Show-Info "All games toggled $(if($newState){'ON'}else{'OFF'})"
        }
        elseif ($input -eq "a") { Invoke-CastSetup; $cfg = Get-Config; $configGames = @($cfg.games.PSObject.Properties.Name) }
        elseif ($input -eq "p") { Invoke-Prep; return }
        elseif ($input -eq "s") {
            # Check which are ON, have video and key
            $toStart = $menuItems | Where-Object { $_.active -and $_.hasVideo -and $_.hasKey }
            $problems = $menuItems | Where-Object { $_.active -and (-not $_.hasVideo -or -not $_.hasKey) }
            
            if ($problems.Count -gt 0) {
                Write-Host ""
                Show-Warn "Some active games have issues:"
                foreach ($p in $problems) {
                    if (-not $p.hasVideo) { Show-Error "  $($p.game): no video file (run Prep)" }
                    if (-not $p.hasKey) { Show-Error "  $($p.game): no RTMP key (run Setup)" }
                }
                Write-Host ""
                $startAnyway = Get-YesNo "Start anyway (skip problematic games)?"
                if (-not $startAnyway) { continue }
            }
            
            if ($toStart.Count -eq 0) {
                Show-Warn "No games ready to broadcast."
                Pause-And-Continue
                continue
            }
            
            Invoke-CastStream -Games $toStart
            return  # after cast ends, go back to main menu
        }
        else {
            # Try number input
            $num = 0
            if ([int]::TryParse($input, [ref]$num)) {
                $item = $menuItems | Where-Object { $_.index -eq $num }
                if ($item) {
                    $newState = -not $item.active
                    Set-GameActive -GameName $item.game -Active $newState
                    if (-not $item.hasVideo) {
                        Show-Warn "'$($item.game)' has no video. Press P to go to Prep."
                    }
                    if (-not $item.hasKey) {
                        Show-Warn "'$($item.game)' has no RTMP key. Press A to go to Setup."
                    }
                }
            }
        }
    }
}

function Invoke-CastStream {
    param($Games)
    
    Write-Banner
    Write-Host "=== 🔴 STARTING BROADCAST ===" -ForegroundColor $Script:CRed
    Write-Host ""
    
    # Make sure log dir exists
    if (-not (Test-Path $Script:LogDir)) { New-Item -ItemType Directory -Path $Script:LogDir -Force | Out-Null }
    
    $Script:ActiveStreams = @{}
    $rtmpUrl = "rtmp://ingest-rtmp.broadcast.steamcontent.com/app"
    
    # Start each stream
    foreach ($game in $Games) {
        # Sanitize game name for filesystem safety
        $safeGameName = $game.game -replace '[<>:"/\\|?*]', '_'
        if ($safeGameName.Length -gt 80) { $safeGameName = $safeGameName.Substring(0, 80) }
        
        $videoPath = Join-Path $Script:OutputDir "$safeGameName.mp4"
        if (-not (Test-Path $videoPath)) {
            Show-Error "Video not found for '$($game.game)' — skipping."
            continue
        }
        
        $rtmpKey = Get-RTMPKey $game.game
        if ([string]::IsNullOrWhiteSpace($rtmpKey)) {
            Show-Error "No RTMP key for '$($game.game)' — skipping."
            continue
        }
        
        $logFile = Join-Path $Script:LogDir "$($game.game)_cast.log"
        $streamUrl = "$rtmpUrl/$rtmpKey"
        
        Show-Step "Starting stream for $($game.game)..."
        
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Script:FFmpegPath
        $psi.Arguments = "-re -y -stream_loop -1 -i `"$videoPath`" -c copy -f flv `"$streamUrl`""
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $psi.WorkingDirectory = $Script:RootDir
        
        $proc = [System.Diagnostics.Process]::Start($psi)
        
        # Assign to job object so it dies when terminal closes
        if ($Script:JobHandle -ne [System.IntPtr]::Zero) {
            [WinJob]::AssignProcessToJobObject($Script:JobHandle, $proc.Handle) | Out-Null
        }
        
        # Stream stderr to log file
        $logStream = [System.IO.StreamWriter]::new($logFile, $false)
        $proc.ErrorDataReceived += {
            param($sender, $e)
            if ($e.Data -ne $null) { $logStream.WriteLine($e.Data); $logStream.Flush() }
        }
        $proc.BeginErrorReadLine()
        
        $Script:ActiveStreams[$game.game] = @{
            PID       = $proc.Id
            Process   = $proc
            StartTime = Get-Date
            LogFile   = $logFile
            LogStream = $logStream
            RTMPKey   = $rtmpKey
        }
        
        Show-Ok "$($game.game) started (PID $($proc.Id))"
        Start-Sleep 1  # stagger starts
    }
    
    if ($Script:ActiveStreams.Count -eq 0) {
        Show-Error "No streams started."
        Pause-And-Continue
        return
    }
    
    # --- Monitor loop ---
    Write-Host ""
    Write-Host "=== 🔴 CASTING — Press Q to stop all === (Escape also quits)" -ForegroundColor $Script:CRed
    
    $casting = $true
    while ($casting) {
        Write-Host "`n"  # blank line
        
        foreach ($gameName in $Script:ActiveStreams.Keys | Sort-Object) {
            $stream = $Script:ActiveStreams[$gameName]
            $running = $stream.Process.HasExited -eq $false
            
            if ($running) {
                $duration = [math]::Floor(((Get-Date) - $stream.StartTime).TotalSeconds)
                $timeStr = "{0:D2}:{1:D2}:{2:D2}" -f [math]::Floor($duration/3600), [math]::Floor(($duration%3600)/60), ($duration%60)
                $bitrate = Get-StreamBitrate $stream.LogFile
                $streamTime = Get-StreamTime $stream.LogFile
                Write-Host "  $gameName  [●] RUNNING  ($timeStr)  $bitrate  PID $($stream.PID)" -ForegroundColor $Script:CGreen
            } else {
                Write-Host "  $gameName  [x] STOPPED" -ForegroundColor $Script:CRed
            }
        }
        
        # Check for keypress (non-blocking)
        try {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.Key -eq [ConsoleKey]::Q) {
                    $casting = $false
                }
            }
        } catch {
            # Non-console host (ISE, remote) — use timeout-based check
        }
        
        if ($casting) {
            Start-Sleep -Milliseconds 2000
        }
    }
    
    # --- Stop all ---
    Write-Host "`n=== Stopping all streams ===" -ForegroundColor $Script:CYellow
    
    foreach ($gameName in $Script:ActiveStreams.Keys) {
        $stream = $Script:ActiveStreams[$gameName]
        if (-not $stream.Process.HasExited) {
            Show-Step "Stopping $gameName (PID $($stream.PID))..."
            $stream.Process.Kill()
            $stream.Process.WaitForExit(5000) | Out-Null
        }
        $stream.LogStream.Close()
        
        # Redact RTMP key from log file
        if ($stream.RTMPKey -and (Test-Path $stream.LogFile)) {
            try {
                $content = Get-Content $stream.LogFile -Raw
                $content = $content -replace [regex]::Escape($stream.RTMPKey), "***REDACTED***"
                Set-Content $stream.LogFile $content
            } catch {
                # Skip if log file can't be read/written
            }
        }
        
        Show-Ok "$gameName stopped"
    }
    
    # Reset all toggles
    foreach ($gName in $Script:ActiveStreams.Keys) { Set-GameActive -GameName $gName -Active $false }
    $Script:ActiveStreams = @{}
    
    Write-Host "`n[√] All streams stopped." -ForegroundColor $Script:CGreen
    Pause-And-Continue
}

#endregion

#region ─── MAIN MENU ──────────────────────────────────────────────────

function Show-MainMenu {
    $exitApp = $false
    
    # Check for newer version (non-blocking)
    Check-NewerVersion
    
    # Initialize job object on first run
    if ($Script:JobHandle -eq [System.IntPtr]::Zero) {
        Initialize-JobObject
    }
    
    while (-not $exitApp) {
        Write-Banner
        
        # Check for orphaned processes
        $orphans = @()
        if ($Script:JobHandle -ne [System.IntPtr]::Zero) {
            # Check if any orphaned ffmpeg from prev session
            $runningFF = Get-Process "ffmpeg" -ErrorAction SilentlyContinue
            if ($runningFF -and $Script:ActiveStreams.Count -eq 0) {
                $orphans = $runningFF
            }
        }
        
        if ($orphans.Count -gt 0) {
            Show-Warn "Found $($orphans.Count) orphaned ffmpeg process(es)."
            $kill = Get-YesNo "Kill them?"
            if ($kill) {
                $orphans | ForEach-Object { $_.Kill() }
                Show-Ok "Cleaned up."
            }
        }
        
        Write-Host ""
        Write-Host "  [1] Prepare Videos (PREP)" -ForegroundColor $Script:CWhite
        Write-Host "      Convert + concatenate game trailers" -ForegroundColor $Script:CCyan
        Write-Host ""
        Write-Host "  [2] Manage Broadcast (CAST)" -ForegroundColor $Script:CWhite
        Write-Host "      Set up keys, toggle streams, start/stop" -ForegroundColor $Script:CCyan
        Write-Host ""
        Write-Host "  [3] Setup (RTMP Keys)" -ForegroundColor $Script:CWhite
        Write-Host "      Add or edit game names and stream keys" -ForegroundColor $Script:CCyan
        Write-Host ""
        Write-Host "  [Q] Quit" -ForegroundColor $Script:CRed
        Write-Host ""
        Write-Host "Select option: " -NoNewline -ForegroundColor $Script:CCyan
        
        $choice = "$([Console]::ReadLine())".Trim().ToLower()
        
        switch ($choice) {
            "1" { Invoke-Prep }
            "2" { Invoke-Cast }
            "3" { Invoke-CastSetup }
            "q" { $exitApp = $true }
            default { Show-Warn "Invalid option." }
        }
    }
    
    Cleanup-JobObject
    Write-Host "`nGoodbye!" -ForegroundColor $Script:CGreen
}

#endregion

#region ─── ENTRY POINT ────────────────────────────────────────────────

# Support command-line arguments
$cmd = "$($args[0])".ToLower()

try {
    switch ($cmd) {
        "prep"    { 
            Check-NewerVersion
            if (-not (Test-FFmpegAvailable)) { $null = Invoke-FFmpegDownload }
            Initialize-JobObject
            try {
                Invoke-Prep
            } finally {
                Cleanup-JobObject
            }
        }
        "setup"   { 
            Check-NewerVersion
            Initialize-JobObject
            try {
                Invoke-CastSetup
            } finally {
                Cleanup-JobObject
            }
        }
        "cast"    { 
            Check-NewerVersion
            if (-not (Test-FFmpegAvailable)) { $null = Invoke-FFmpegDownload }
            Initialize-JobObject
            try {
                Invoke-Cast
            } finally {
                Cleanup-JobObject
            }
        }
        default   { 
            Initialize-JobObject
            try {
                Show-MainMenu
            } finally {
                Cleanup-JobObject
            }
        }
    }
} catch {
    Write-Host ""
    Write-Host "╔════════════════════════════════════════════╗" -ForegroundColor Red
    Write-Host "║            UNHANDLED ERROR                 ║" -ForegroundColor Red
    Write-Host "╚════════════════════════════════════════════╝" -ForegroundColor Red
    Write-Host ""
    Write-Host "Message:     $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Line:        $($_.InvocationInfo.ScriptLineNumber)" -ForegroundColor Red
    Write-Host "Position:    $($_.InvocationInfo.OffsetInLine)" -ForegroundColor Red
    Write-Host "StackTrace:" -ForegroundColor Yellow
    Write-Host "$($_.ScriptStackTrace)" -ForegroundColor Gray
    Write-Host ""
    Write-Host "[i] Report this at https://github.com/underagum/steamcast/issues" -ForegroundColor Cyan
}

# Always pause before exit — even on crash
Write-Host "`nPress any key to exit..." -ForegroundColor Cyan
try { $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") } catch { Start-Sleep 5 }