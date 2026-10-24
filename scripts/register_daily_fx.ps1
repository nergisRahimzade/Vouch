# Registers a Windows scheduled task that refreshes exchange rates once a day at 07:00.
# Remove it with:  schtasks /Delete /TN "Vouch FX refresh" /F
$repo = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python).Source
$action = "cmd /c cd /d `"$repo`" && `"$python`" -m vouch.reference.fetch_fx >> fx_refresh.log 2>&1"
schtasks /Create /TN "Vouch FX refresh" /TR $action /SC DAILY /ST 07:00 /F
