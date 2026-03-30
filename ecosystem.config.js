module.exports = {
  apps: [{
    name: 'auto-apply-watch',
    script: 'main.py',
    interpreter: 'C:\\Users\\ok\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe',
    interpreter_args: '-u',
    args: '--watch',
    cwd: 'C:\\Users\\ok\\xblock-auto-apply',
    watch: false,
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 60000,
  }]
};
