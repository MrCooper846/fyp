$ErrorActionPreference = "Continue"
$PythonExe = "C:\Users\monke\anaconda3\envs\py312\python.exe"

New-Item -ItemType Directory -Force logs | Out-Null

Write-Host "Starting Italy legacy run: $(Get-Date)"
& $PythonExe gc_contacts_cli.py IT --outfile italy_contacts.csv *> logs\italy_run.log

Write-Host "Starting Spain legacy run: $(Get-Date)"
& $PythonExe gc_contacts_cli.py ES --outfile spain_contacts.csv *> logs\spain_run.log

Write-Host "Starting Belgium legacy run: $(Get-Date)"
& $PythonExe gc_contacts_cli.py BE --outfile belgium_contacts.csv *> logs\belgium_run.log

Write-Host "Starting Italy NAFSA run: $(Get-Date)"
& $PythonExe gc_contacts_cli.py nafsa IT --output Nafsaitaly_contacts.csv *> logs\nafsa_italy_run.log

Write-Host "Starting Spain NAFSA run: $(Get-Date)"
& $PythonExe gc_contacts_cli.py nafsa ES --output Nafsaspain_contacts.csv *> logs\nafsa_spain_run.log

Write-Host "Starting Belgium NAFSA run: $(Get-Date)"
& $PythonExe gc_contacts_cli.py nafsa BE --output Nafsabelgium_contacts.csv *> logs\nafsa_belgium_run.log

Write-Host "All overnight runs finished: $(Get-Date)"
