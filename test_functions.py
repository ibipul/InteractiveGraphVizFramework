import json
from pprint import pprint
def print_graph_details(json_data):
    nodes = json_data["nodes"]
    links = json_data["links"]
    print("=====================\n\nNow The Nodes are:: ")
    pprint(nodes)
    print("\n\nNow The Links are:: ")
    pprint(links)
    print("\n======================")

def create_adjacency_matrix(json_data):
    pass

def file_update_and_state_reset_test(json_data):
    node_group_toggle = 0
    node_list = []
    for key in json_data.keys():
        if key == 'nodes':
            nodes = json_data[key]
            for element in nodes:
                print("ID: ", element['id']," Grp: ",element['group'])
                if (node_group_toggle % 2) ==0:
                    node_list.append({"id": int(element['id']), "group": int(element['group']) %2 + 2})
                else:
                    node_list.append({"id": int(element['id']), "group": int(element['group']) %2 + 1})

                node_group_toggle +=1
    print("Changed graph Structure:")
    graph_dict = {"nodes": node_list, "links": json_data["links"]}
    pprint(graph_dict)
    try:
        with open('static/miserables3.json', 'w') as fp:
            json.dump(graph_dict, fp, indent=4, sort_keys=True, separators=(',', ': '))
    except Exception:
        print("Some file writing error has occurred")
        return -1
    return 0