# Project romulus
This repo will contain all the work associated with creation of interactive graph viz interface. 
Basic UI code has been lifted from d3.js examples by Ross Kirsling, under MIT License.

## Project
Here's how the project is proceeding
- *March 12*, Basic tests to understand bits and pieces
- *March 13*, Figured out asynchronous data read for JS
- *March 14*, Data transfer from UI to backend/ progress project is flasktest
- *March 15*, 
	-- Fixed json structure (close to networkx)
	-- Nodes get colored by group id
	-- Cleaned Json for communicating with backend
- *March 20*, Redesign graph details based on latest design discussion with prof.
- *March 25*,
	- Graph Snapshot feature with pretty print
	- Graph write back finalized
	- Reload from disk on update state call!
- *March 26*,
	- Add or decrease load on nodes on some event implemented
	- Stub module plug and play tested out.
- *March 27*
	- Fixed the Caching of json file issue, in absense of dev console

- [Next Milestones]
	- epic animantions (very stretch goal)
	- test on high node densities
	- node & link color features

