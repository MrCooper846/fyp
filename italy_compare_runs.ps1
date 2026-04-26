$ErrorActionPreference = "Continue"

New-Item -ItemType Directory -Force logs | Out-Null
New-Item -ItemType Directory -Force debug_logs\legacy_italy | Out-Null
New-Item -ItemType Directory -Force debug_logs\nafsa_italy | Out-Null

conda activate py312

Write-Host "Starting Italy legacy run: $(Get-Date)"
python gc_contacts_cli.py IT --outfile italy_contacts_legacy.csv --debug --debug-dir debug_logs\legacy_italy *> logs\italy_legacy_compare.log

Write-Host "Starting Italy NAFSA run: $(Get-Date)"
python gc_contacts_cli.py nafsa IT --output italy_contacts_nafsa.csv --debug --debug-dir debug_logs\nafsa_italy *> logs\italy_nafsa_compare.log

Write-Host "Italy comparison runs finished: $(Get-Date)"
