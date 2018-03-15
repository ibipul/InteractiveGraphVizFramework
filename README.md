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
- [Next Mile stones]:
	- Current communication model is Start, push data back, update file, reload.
	- Figure out smooth auto transition model/ Ask Ankur
	- Figure out edit option for node group id, value and node weight/ Ask Ankur
