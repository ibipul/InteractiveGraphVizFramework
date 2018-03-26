from flask import Flask
from flask import render_template
from flask_socketio import SocketIO
from flask_socketio import emit

from test_functions import print_graph_details, file_update_and_state_reset_test


app = Flask(__name__)
app.config['SECRET_KEY'] = 'ibipul_is_awesome'
socketio = SocketIO(app)


@app.route("/")
def index():
    return render_template("index.html")

@socketio.on('json')
def handle_json(json_str):
    print("Length of json object", len(json_str))
    print_graph_details(json_str)

@socketio.on('update')
def handle_json(u_json_str):
    print("Length of json object", len(u_json_str))
    status = file_update_and_state_reset_test(u_json_str)
    if status ==0:
        emit('graph_update', {'update': True})
    else:
        print("Update not possible file writing error happened")
        # emit('graph_update', {'update': False})

#
@socketio.on('my event')
def handle_my_custom_event(json):
    print('On Connect recieved: ' + str(json))

if __name__ == "__main__":
    #app.run(debug=True)
    socketio.run(app, debug=True)