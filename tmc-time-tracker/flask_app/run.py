import os
from app import create_app, db
from app.models import CompanyConfig, User # Import models to ensure they are known to Flask-Migrate

app = create_app()

# A shell context for Flask-Migrate and other CLI tools
@app.shell_context_processor
def make_shell_context():
    return {'db': db, 'User': User, 'CompanyConfig': CompanyConfig}

if __name__ == '__main__':
    # Use '0.0.0.0' to be accessible from the network, including from the Electron app in dev
    app.run(host='0.0.0.0', port=5000, debug=True)