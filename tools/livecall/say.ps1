Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 0
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
$lines = @(
  @('d1', "Hey there. Could you give a shout out on air for my friend Marcus? He's been having a rough week."),
  @('d2', "That's great, thank you. I'll wait while it goes out."),
  @('d3', "Perfect. Thanks so much, I'll let you get back to it. Bye now.")
)
foreach ($l in $lines) {
  $s.SetOutputToWaveFile("audio\$($l[0]).wav", $fmt)
  $s.Speak($l[1])
}
$s.SetOutputToNull(); $s.Dispose()
Write-Output "spoken"
