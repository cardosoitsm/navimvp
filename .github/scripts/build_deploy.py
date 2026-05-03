import base64, os

compose_b64 = base64.b64encode(open('docker-compose.prod.yml', 'rb').read()).decode()
db_sql_b64  = base64.b64encode(open('db/init.sql', 'rb').read()).decode()

env_content = '\n'.join([
      'ACR_REGISTRY='     + os.environ['ACR_REGISTRY'],
      'ACCOUNT_SID='      + os.environ['ACCOUNT_SID'],
      'AUTH_TOKEN='       + os.environ['AUTH_TOKEN'],
      'TWILIO_NUMBER='    + os.environ['TWILIO_NUMBER'],
      'OPENAI_API_KEY='   + os.environ['OPENAI_API_KEY'],
      'SECRET_KEY='       + os.environ['SECRET_KEY'],
      'ADMIN_SECRET_KEY=' + os.environ.get('ADMIN_SECRET_KEY', ''),
      'ALGORITHM=HS256',
      'DATABASE_PASSWORD=' + os.environ['DATABASE_PASSWORD'],
])

env_b64  = base64.b64encode(env_content.encode()).decode()
acr_pass = base64.b64encode(os.environ['ACR_PASSWORD'].encode()).decode()

template = open('.github/scripts/deploy_template.sh').read()
script = (template
              .replace('__USER__',     os.environ['VM_SSH_USER'])
              .replace('__COMPOSE__',  compose_b64)
              .replace('__DB_SQL__',   db_sql_b64)
              .replace('__ENV__',      env_b64)
              .replace('__ACR_PASS__', acr_pass)
              .replace('__ACR_REG__',  os.environ['ACR_REGISTRY'])
              .replace('__ACR_USER__', os.environ['ACR_USERNAME'])
         )

open('/tmp/deploy.sh', 'w').write(script)
print('Deploy script built successfully')
