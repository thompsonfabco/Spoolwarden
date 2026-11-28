<#
.SYNOPSIS
  Creates and manages the filament-kit Android release keystore.

.DESCRIPTION
  Default (no switch): create %LOCALAPPDATA%\filament-kit\release.jks once, with a random
  password stored via DPAPI in keystore.pw.dpapi. Refuses to overwrite an existing keystore.
  -Print   write the password to stdout (for make_release.sh; never log it)
  -Forget  delete the keystore AND the DPAPI password file (asks first; -Force skips the question)
  -Export <path>  write a passphrase-protected backup (.zip-less single file) for another PC;
                  the backup passphrase must be at least 12 characters
  -Import <path>  restore from such a backup (refuses to overwrite)
The password is generated here (CSPRNG); you never type it. A DPAPI file only decrypts on this
Windows account, so keep an -Export backup somewhere safe as well.

  FK_BACKUP_PASSPHRASE (environment): TEST AUTOMATION ONLY. When set, -Export/-Import use it instead
  of prompting. Never set it for a real backup: environment variables can be read by other processes
  of this user and may end up in shell history or logs.
#>
[CmdletBinding()]
param(
    [switch]$Print,
    [switch]$Forget,
    [switch]$Force,
    [string]$Export,
    [string]$Import
)
$ErrorActionPreference = 'Stop'

$dir = Join-Path $env:LOCALAPPDATA 'filament-kit'
$jks = Join-Path $dir 'release.jks'
$pwFile = Join-Path $dir 'keystore.pw.dpapi'
# The JDK in JAVA_HOME, else keytool on PATH.
$keytool = 'keytool.exe'
if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME 'bin\keytool.exe'))) {
    $keytool = Join-Path $env:JAVA_HOME 'bin\keytool.exe'
}
$MinPassphrase = 12

function Read-StoredPassword {
    if (-not (Test-Path $pwFile)) { throw "No password file at $pwFile" }
    $ss = (Get-Content -LiteralPath $pwFile -Raw).Trim() | ConvertTo-SecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Save-Password([string]$plain) {
    $ss = ConvertTo-SecureString $plain -AsPlainText -Force
    ConvertFrom-SecureString $ss | Set-Content -LiteralPath $pwFile -Encoding ascii
}

function New-RandomPassword {
    $chars = [char[]]'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })   # 48 chars
}

function Read-Passphrase([string]$prompt, [switch]$Confirm) {
    if ($env:FK_BACKUP_PASSPHRASE) { return (ConvertTo-SecureString $env:FK_BACKUP_PASSPHRASE -AsPlainText -Force) }   # automation/tests only
    $a = Read-Host $prompt -AsSecureString
    if ($Confirm) {
        $b = Read-Host 'Repeat passphrase' -AsSecureString
        $pa = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($a))
        $pb = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($b))
        if ($pa -ne $pb) { throw 'Passphrases do not match.' }
    }
    $a
}

if ($Print) {
    Write-Output (Read-StoredPassword)
    exit 0
}

if ($Forget) {
    if (-not $Force) {
        $ans = Read-Host "Delete $jks and $pwFile? Without a backup, installed apps can never be updated. Type YES"
        if ($ans -ne 'YES') { Write-Host 'Aborted.'; exit 1 }
    }
    Remove-Item -LiteralPath $jks, $pwFile -Force -ErrorAction SilentlyContinue
    Write-Host 'Keystore and password file deleted.'
    exit 0
}

if ($Export) {
    if (-not (Test-Path $jks) -or -not (Test-Path $pwFile)) { throw 'Nothing to export: keystore or password file missing.' }
    if (Test-Path $Export) { throw "Refusing to overwrite $Export" }
    $pass = Read-Passphrase "Backup passphrase, at least $MinPassphrase characters (you will need it to restore)" -Confirm
    if ($pass.Length -lt $MinPassphrase) { throw "The backup passphrase must be at least $MinPassphrase characters; nothing was written." }
    # Passphrase-derived AES-256 + HMAC-SHA256 keys (PBKDF2-SHA256, 600k iterations); payload = JSON {jks b64, pw}.
    $payload = [Text.Encoding]::UTF8.GetBytes((@{
        jks = [Convert]::ToBase64String([IO.File]::ReadAllBytes($jks))
        pw  = (Read-StoredPassword)
    } | ConvertTo-Json -Compress))
    $salt = New-Object byte[] 16; $iv = New-Object byte[] 16
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create(); $rng.GetBytes($salt); $rng.GetBytes($iv); $rng.Dispose()
    $plainPass = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pass))
    $kdf = New-Object Security.Cryptography.Rfc2898DeriveBytes($plainPass, $salt, 600000, [Security.Cryptography.HashAlgorithmName]::SHA256)
    $derived = $kdf.GetBytes(64); $kdf.Dispose()
    $encKey = [byte[]]$derived[0..31]
    $macKey = [byte[]]$derived[32..63]
    $aes = [Security.Cryptography.Aes]::Create(); $aes.Key = $encKey; $aes.IV = $iv
    $cipher = $aes.CreateEncryptor().TransformFinalBlock($payload, 0, $payload.Length); $aes.Dispose()
    $headerAndCipher = [byte[]]([Text.Encoding]::ASCII.GetBytes('FKKS2') + $salt + $iv + $cipher)
    $hmac = [Security.Cryptography.HMACSHA256]::new($macKey)
    try { $tag = $hmac.ComputeHash($headerAndCipher) } finally { $hmac.Dispose() }
    $out = $headerAndCipher + $tag
    [IO.File]::WriteAllBytes($Export, $out)
    Write-Host "Backup written to $Export (restore with -Import)."
    exit 0
}

if ($Import) {
    if ((Test-Path $jks) -or (Test-Path $pwFile)) { throw 'A keystore already exists here; refusing to overwrite (use -Forget first).' }
    $raw = [IO.File]::ReadAllBytes($Import)
    if ($raw.Length -lt 38) { throw 'Not a filament-kit keystore backup.' }
    $magic = [Text.Encoding]::ASCII.GetString($raw, 0, 5)
    if ($magic -ne 'FKKS2' -and $magic -ne 'FKKS1') { throw 'Not a filament-kit keystore backup.' }
    if ($magic -eq 'FKKS2' -and $raw.Length -lt 70) { throw 'Wrong passphrase or corrupted backup.' }
    $salt = [byte[]]$raw[5..20]; $iv = [byte[]]$raw[21..36]
    $pass = Read-Passphrase 'Backup passphrase'
    $plainPass = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pass))
    $kdf = New-Object Security.Cryptography.Rfc2898DeriveBytes($plainPass, $salt, 600000, [Security.Cryptography.HashAlgorithmName]::SHA256)
    if ($magic -eq 'FKKS2') {
        $derived = $kdf.GetBytes(64); $kdf.Dispose()
        $encKey = [byte[]]$derived[0..31]
        $macKey = [byte[]]$derived[32..63]
        $cipherEnd = $raw.Length - 33
        $headerAndCipher = [byte[]]$raw[0..$cipherEnd]
        $tag = [byte[]]$raw[($cipherEnd + 1)..($raw.Length - 1)]
        $hmac = [Security.Cryptography.HMACSHA256]::new($macKey)
        try { $expectedTag = $hmac.ComputeHash($headerAndCipher) } finally { $hmac.Dispose() }
        $diff = 0
        for ($i = 0; $i -lt 32; $i++) { $diff = $diff -bor ($tag[$i] -bxor $expectedTag[$i]) }
        if ($diff -ne 0) { throw 'Wrong passphrase or corrupted backup.' }
        $cipher = [byte[]]$raw[37..$cipherEnd]
    } else {
        $encKey = $kdf.GetBytes(32); $kdf.Dispose()
        $cipher = [byte[]]$raw[37..($raw.Length - 1)]
    }
    $aes = [Security.Cryptography.Aes]::Create(); $aes.Key = $encKey; $aes.IV = $iv
    # A wrong passphrase can still decrypt to padding-valid garbage, so the JSON/base64 checks belong here too.
    try {
        $plain = $aes.CreateDecryptor().TransformFinalBlock($cipher, 0, $cipher.Length)
        $obj = [Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json
        if (-not $obj.jks -or -not $obj.pw) { throw 'incomplete backup' }
        $jksBytes = [Convert]::FromBase64String($obj.jks)
    }
    catch { throw 'Wrong passphrase or corrupted backup.' }
    finally { $aes.Dispose() }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    # Password first: a keystore without its password file is useless, so never leave one behind.
    Save-Password $obj.pw
    try { [IO.File]::WriteAllBytes($jks, $jksBytes) }
    catch {
        Remove-Item -LiteralPath $jks, $pwFile -Force -ErrorAction SilentlyContinue
        throw "Could not write ${jks}: $($_.Exception.Message)"
    }
    Write-Host "Restored $jks and the DPAPI password file."
    exit 0
}

# ---- create ----
if (Test-Path $jks) {
    Write-Error "Keystore already exists at $jks; refusing to overwrite."
    exit 1
}
if (-not (Test-Path $keytool)) { Write-Error "keytool not found at $keytool"; exit 1 }
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$pw = New-RandomPassword
# Save the password BEFORE the keystore exists: a keystore whose password was never saved can never sign an
# update. On any failure below, both files are removed so the next run starts clean.
try { Save-Password $pw }
catch {
    Remove-Item -LiteralPath $pwFile -Force -ErrorAction SilentlyContinue
    Write-Error "Could not save the keystore password ($($_.Exception.Message)); signing key NOT created."
    exit 1
}
$env:FK_KS_GEN_PW = $pw
$ok = $false
$keytoolOut = @()
try {
    $ErrorActionPreference = 'Continue'   # keytool writes progress to stderr
    & $keytool -genkeypair -keystore $jks -storetype PKCS12 -alias filament-kit `
        -keyalg RSA -keysize 4096 -validity 10000 -dname 'CN=filament-kit' `
        '-storepass:env' FK_KS_GEN_PW '-keypass:env' FK_KS_GEN_PW 2>&1 | ForEach-Object { $keytoolOut += "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    $ok = ($code -eq 0) -and (Test-Path $jks)
} finally {
    Remove-Item Env:FK_KS_GEN_PW -ErrorAction SilentlyContinue
    if (-not $ok) { Remove-Item -LiteralPath $jks, $pwFile -Force -ErrorAction SilentlyContinue }
}
if (-not $ok) {
    # keytool's own words (the password goes in through the environment and is never in its output)
    $keytoolOut | Select-Object -Last 5 | ForEach-Object { Write-Host "keytool: $_" }
    Write-Error "keytool failed (exit $code); signing key NOT created."
    exit 1
}
Write-Host "Created $jks (password stored via DPAPI in $pwFile; never printed)."
Write-Host "BACK UP both files, and run -Export <path> for a copy that works on another PC."
