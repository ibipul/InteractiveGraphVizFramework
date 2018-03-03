import networkx as nx
import json
import matplotlib.pyplot as plt
##%matplotlib inline

G = nx.erdos_renyi_graph(30,4.0/30)
while not nx.is_connected(G):
    G = nx.erdos_renyi_graph(30,4.0/30)
plt.figure(figsize=(6,4))
nx.draw(G)

for ix,deg in G.degree().items():
    G.node[ix]['degree'] = deg
    G.node[ix]['parity'] = (1-deg%2)

for ix,katz in nx.katz_centrality(G).items():
    G.node[ix]['katz'] = katz

print(G.nodes(data=True)[:5])
