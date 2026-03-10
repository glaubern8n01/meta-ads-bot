@echo off
cd /d "C:\Users\ADM\Desktop\trafegotelegram"
:loop
echo [%date% %time%] Iniciando Meta Ads Agent...
python meta_ads_agent.py >> bot_log.log 2>&1
echo [%date% %time%] Bot parou. Reiniciando em 10 segundos...
timeout /t 10
goto loop
