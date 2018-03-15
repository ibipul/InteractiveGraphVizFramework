from flask import Flask
from flask import render_template
from flask_socketio import SocketIO

## Importing custom python functions
from test_functions import print_graph_details

## Specific to Flask initialization
app = Flask(__name__)
app.config['SECRET_KEY'] = 'ibipul_is_awesome'
socketio = SocketIO(app)

## Landing page rendering
@app.route("/")
def index():
    return render_template("index.html")

## Handling on button press event
# Here we just recieve it and display what we got.
@socketio.on('json')
def handle_json(json_str):
    print("Length of json object", len(json_str))
    print_graph_details(json_str)

# Executed when client connects
@socketio.on('my event')
def handle_my_custom_event(json):
    print('On Connect recieved: ' + str(json))

if __name__ == "__main__":
	# Start running in debug mode of socketio
    socketio.run(app, debug=True)