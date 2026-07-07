@echo off
chcp 65001 >nul
echo ================================================
echo  Checagem de instalacao: Postgres e Memurai
echo ================================================
echo.

echo --- [1/4] Servicos do Postgres instalados:
powershell -NoProfile -Command "$s = Get-Service -Name '*postgres*' -ErrorAction SilentlyContinue; if ($s) { $s | Format-Table Name, Status -AutoSize } else { Write-Host 'NENHUM servico Postgres encontrado - Postgres NAO esta instalado' }"

echo --- [2/4] Porta 5432 (Postgres) em escuta?
netstat -an | findstr /C:":5432" | findstr LISTENING >nul
if %errorlevel%==0 (
    echo [OK] Postgres esta RODANDO na porta 5432
) else (
    echo [PENDENTE] Nada escutando na porta 5432 - Postgres nao esta rodando
)
echo.

echo --- [3/4] Servicos do Memurai/Redis instalados:
powershell -NoProfile -Command "$s = Get-Service -Name '*memurai*','*redis*' -ErrorAction SilentlyContinue; if ($s) { $s | Format-Table Name, Status -AutoSize } else { Write-Host 'NENHUM servico Memurai/Redis encontrado - Memurai NAO esta instalado' }"

echo --- [4/4] Porta 6379 (Redis) em escuta?
netstat -an | findstr /C:":6379" | findstr LISTENING >nul
if %errorlevel%==0 (
    echo [OK] Memurai/Redis esta RODANDO na porta 6379
) else (
    echo [PENDENTE] Nada escutando na porta 6379 - Memurai nao esta rodando
)
echo.

echo ================================================
echo  Como interpretar:
echo  - Os itens 2 e 4 com [OK] = infraestrutura pronta.
echo  - Servico instalado mas porta sem escuta = servico
echo    parado. Abra "Servicos" no Windows e clique Iniciar.
echo  - Nenhum servico encontrado = falta instalar.
echo ================================================
echo.
pause
