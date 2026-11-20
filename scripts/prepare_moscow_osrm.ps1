param(
    [Parameter(Mandatory)][string]$Pbf,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [ValidateSet('foot', 'car')][string[]]$Profiles = @('foot', 'car'),
    [string]$Image = 'ghcr.io/project-osrm/osrm-backend:v5.27.1'
)

$ErrorActionPreference = 'Stop'
$source = Get-Item -LiteralPath $Pbf
if ($source.PSIsContainer -or $source.Name -notlike '*.osm.pbf' -or $source.Length -eq 0) {
    throw 'Укажите существующий непустой файл .osm.pbf.'
}

function Invoke-Osrm {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker завершился с кодом $LASTEXITCODE."
    }
}

Invoke-Osrm -Arguments @('version', '--format', '{{.Server.Os}}')
$root = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
foreach ($profile in $Profiles) {
    $target = Join-Path $root $profile
    [void][System.IO.Directory]::CreateDirectory($target)
    if (Get-ChildItem -LiteralPath $target -Filter 'map.osrm*') {
        throw "В $target уже есть файлы графа. Выберите другой каталог сборки."
    }
    $base = @('run', '--rm', '--mount', "type=bind,source=$target,target=/data")
    $sourceMount = "type=bind,source=$($source.FullName),target=/data/map.osm.pbf,readonly"
    Write-Host "Сборка графа OSRM для профиля $profile"
    Invoke-Osrm -Arguments ($base + @('--mount', $sourceMount, $Image, 'osrm-extract', '-p', "/opt/$profile.lua", '/data/map.osm.pbf'))
    Invoke-Osrm -Arguments ($base + @($Image, 'osrm-partition', '/data/map.osrm'))
    Invoke-Osrm -Arguments ($base + @($Image, 'osrm-customize', '/data/map.osrm'))
    $port = if ($profile -eq 'foot') { 5001 } else { 5002 }
    Write-Host "Запуск сервера для профиля $profile"
    Write-Host "docker run --rm -p 127.0.0.1:${port}:5000 --mount `"type=bind,source=$target,target=/data,readonly`" $Image osrm-routed --algorithm mld /data/map.osrm"
}
