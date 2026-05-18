@echo off
echo Setting up Claude Code agents for Navi...

if not exist ".claude\commands" (
    mkdir ".claude\commands"
    echo Created .claude\commands directory.
)

copy /Y "agents\commands\pm.md" ".claude\commands\pm.md" >nul
copy /Y "agents\commands\dev.md" ".claude\commands\dev.md" >nul
copy /Y "agents\commands\qa.md" ".claude\commands\qa.md" >nul

echo.
echo Done! Agents installed:
echo   /pm  ^>  .claude\commands\pm.md
echo   /dev ^>  .claude\commands\dev.md
echo   /qa  ^>  .claude\commands\qa.md
echo.
echo Open Claude Code inside navimvp\ and run /pm, /dev or /qa to activate them.
pause
