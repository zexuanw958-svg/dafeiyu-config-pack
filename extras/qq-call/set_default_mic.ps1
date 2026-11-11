param(
    [Parameter(Mandatory = $true)]
    [string]$DeviceName,
    [string]$DllPath = ""
)

$ErrorActionPreference = "Stop"
if (-not $DllPath) {
    $DllPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "AudioDeviceCmdlets.dll"
}
if (-not (Test-Path $DllPath)) {
    throw "找不到 AudioDeviceCmdlets.dll。请从 https://github.com/frgnca/AudioDeviceCmdlets/releases 下载，和本脚本放同一目录，或用 -DllPath 指定。"
}

Import-Module $DllPath -ErrorAction Stop
$device = Get-AudioDevice -List | Where-Object { $_.Type -eq "Recording" -and $_.Name -eq $DeviceName } | Select-Object -First 1
if (-not $device) {
    Write-Host "当前录音设备："
    Get-AudioDevice -List | Where-Object { $_.Type -eq "Recording" } | Select-Object Index, Name, Default, DefaultCommunication | Format-Table -AutoSize
    throw "没有找到录音设备：$DeviceName"
}

Set-AudioDevice -Index $device.Index -DefaultOnly
Set-AudioDevice -Index $device.Index -CommunicationOnly
Write-Host "默认录音已切换到：$($device.Name)"
Get-AudioDevice -List | Where-Object { $_.Type -eq "Recording" -and ($_.Default -or $_.DefaultCommunication) } | Select-Object Index, Name, Default, DefaultCommunication | Format-Table -AutoSize
