param([string]$Job)
$ErrorActionPreference = 'Stop'
$data = Get-Content -LiteralPath $Job -Raw -Encoding UTF8 | ConvertFrom-Json
$voice = New-Object -ComObject SAPI.SpVoice
foreach ($item in $data) {
    $token = New-Object -ComObject SAPI.SpObjectToken
    $token.SetId(('HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens\' + $item.token), '', $false)
    $voice.Voice = $token
    $voice.Rate = [int]$item.rate
    $stream = New-Object -ComObject SAPI.SpFileStream
    try {
        $stream.Format.Type = 22
        $stream.Open($item.output, 3, $false)
        $voice.AudioOutputStream = $stream
        $escaped = [System.Security.SecurityElement]::Escape([string]$item.text)
        $xml = '<pitch absmiddle="' + [int]$item.pitch + '">' + $escaped + '</pitch>'
        [void]$voice.Speak($xml, 8)
    } finally {
        $stream.Close()
    }
}
