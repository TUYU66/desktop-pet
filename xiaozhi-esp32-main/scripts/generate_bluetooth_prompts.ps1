param([string]$Ffmpeg = 'D:/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'zh-CN' } | Select-Object -First 1
if (-not $voice) { throw 'An installed zh-CN speech voice is required.' }
$synth.SelectVoice($voice.VoiceInfo.Name)
$projectRoot = Split-Path $PSScriptRoot -Parent
$temporaryRoot = Join-Path (Split-Path $projectRoot -Parent) 'tmp/bluetooth-prompts'
New-Item -ItemType Directory -Path $temporaryRoot -Force | Out-Null
$prompts = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'bluetooth_prompts.json') -Raw -Encoding UTF8 | ConvertFrom-Json
try {
    foreach ($prompt in $prompts.PSObject.Properties) {
        $wav = Join-Path $temporaryRoot ($prompt.Name + '.wav')
        $ogg = Join-Path $projectRoot ('main/assets/common/' + $prompt.Name + '.ogg')
        $synth.SetOutputToWaveFile($wav)
        $synth.Speak([string]$prompt.Value)
        $synth.SetOutputToNull()
        & $Ffmpeg -hide_banner -loglevel error -y -i $wav -ac 1 -ar 24000 -c:a libopus -b:a 24000 -application voip -frame_duration 60 $ogg
        if ($LASTEXITCODE -ne 0) { throw 'Opus conversion failed.' }
        Write-Output ($prompt.Name + ' ready')
    }
} finally { $synth.Dispose() }
