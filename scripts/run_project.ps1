param(
    [Parameter(Mandatory = $true)]
    [string]$Config
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================"
Write-Host " YOUTUBE DATA PIPELINE"
Write-Host " Config: $Config"
Write-Host "========================================"
Write-Host ""

# 1. Valida o YAML antes de gastar quota.
Write-Host ">>> Validando configuração..."

youtube-pipeline validate-config $Config

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERRO: configuração inválida. Execução interrompida."
    exit $LASTEXITCODE
}

# O primeiro bloco precisa conter discovery + videos,
# pois o pipeline atual exige a tabela videos após o discovery.
$stages = @(
    @{
        Name = "Discovery + Videos"
        Value = "discovery,videos"
    },
    @{
        Name = "Channels"
        Value = "channels"
    },
    @{
        Name = "Transcripts"
        Value = "transcripts"
    },
    @{
        Name = "Comments"
        Value = "comments"
    }
)

foreach ($stage in $stages) {

    Write-Host ""
    Write-Host "========================================"
    Write-Host " INICIANDO: $($stage.Name)"
    Write-Host "========================================"
    Write-Host ""

    youtube-pipeline run $Config --stages $stage.Value

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "========================================"
        Write-Host " ERRO NA ETAPA: $($stage.Name)"
        Write-Host " Pipeline interrompido."
        Write-Host "========================================"
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "OK: $($stage.Name) concluído."
}

Write-Host ""
Write-Host "========================================"
Write-Host " PIPELINE CONCLUÍDO COM SUCESSO"
Write-Host "========================================"