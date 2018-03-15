def print_graph_details(json_data):
    nodes = json_data["nodes"]
    links = json_data["links"]
    print("=====================\n\nNow The Nodes are:: ")
    print(nodes)
    print("\n\nNow The Links are:: ")
    print(links)
    print("\n======================")