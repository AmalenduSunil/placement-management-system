from flask import Flask
from model import *

def create_app():
    app= Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"]= "sqlite:///placement_db_sqlite3.db"
    db.init_app(app)
    app.app_context().push()
    db.drop_all()
    db.create_all()
    return app

app = create_app()

# from initial_db import *
from routes import *

if __name__=="__main__":
    app.run(debug=True)