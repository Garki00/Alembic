# scripts/verify-sandbox.ps1
# 发布前/安装后验证：检查用户、防火墙和配置是否完整。
param()
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "setup-sandbox.ps1") -Verify
