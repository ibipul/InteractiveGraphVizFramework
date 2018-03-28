var socket = io.connect('http://' + document.domain + ':' + location.port);
  socket.on('connect', function() {
  socket.emit('my event', {data: 'I\'m connected!'});
  console.log('data Sent');
  });
  // set up SVG for D3
  var width  = 960,
      height = 500,
      colors = d3.scale.category10();

  var svg = d3.select('body')
    .append('svg')
    .attr('oncontextmenu', 'return false;')
    .attr('width', width)
    .attr('height', height);

    // Defining a set of call-back functions to access data in the
    // d3.json anynchronous function

    var jsondata;
    function getJsonData(){
        //console.log(jsondata);
        return jsondata;
    }
    var nodes;
    function getNodes(){
        return nodes;
    }
    var links;
    function getLinkdata(){
        return links;
    }
    var lastNodeId;
    function getLastNodeId(){
        return lastNodeId;
    }

function thatfunction(){
  var noCache = new Date().getTime();
  // Reads and loads a graph
  d3.json("/static/miserables4.json?_=" + noCache, function(error, data) {
  if (error) throw error;

      var graph_data = data
      nodes = graph_data.nodes;
      getNodes(); // throwing out node data

      linkdata = graph_data.links;
      links = [];
      var x;
      // This function prevents against unbounded access
      // Will make things slow for large number of nodes (say millions)
      function findNode(id){
        for(i in nodes){
         if (nodes[i].id == id) return nodes[i];
        }
      }
      // read link information and build the structure
      for (x in linkdata) {
        n = {source: findNode(linkdata[x]["source"]),target:findNode(linkdata[x]["target"]),
            lval:linkdata[x]["lval"],current:linkdata[x]["current"],total:linkdata[x]["total"]};
        links.push(n);
      }
      getLinkdata(); // throwing out link data
      // Getting the last node which is sorted by index
      var temp = nodes[nodes.length - 1];
      lastNodeId = temp["id"]
      getLastNodeId() // throwing id of last node in the node array, always sorted by id.



  // init D3 force layout
  var force = d3.layout.force()
      .nodes(nodes)
      .links(links)
      .size([width, height])
      .linkDistance(30)
      .charge(-85)
      .on('tick', tick)

  // line displayed when dragging new nodes
  var drag_line = svg.append('svg:path')
    .attr('class', 'link dragline hidden')
    .attr('d', 'M0,0L0,0');

  // handles to link and node element groups
  var path = svg.append('svg:g').selectAll('path'),
      circle = svg.append('svg:g').selectAll('g');

  // mouse event vars
  var selected_node = null,
      selected_link = null,
      mousedown_link = null,
      mousedown_node = null,
      mouseup_node = null;

  function resetMouseVars() {
    mousedown_node = null;
    mouseup_node = null;
    mousedown_link = null;
  }

  // update force layout (called automatically each iteration)
  function tick() {
    // draw directed edges with proper padding from node centers
    path.attr('d', function(d) {
      var deltaX = d.target.x - d.source.x,
          deltaY = d.target.y - d.source.y,
          dist = Math.sqrt(deltaX * deltaX + deltaY * deltaY),
          normX = deltaX / dist,
          normY = deltaY / dist,
          sourcePadding = 10,
          targetPadding = 10,
          sourceX = d.source.x + (sourcePadding * normX),
          sourceY = d.source.y + (sourcePadding * normY),
          targetX = d.target.x - (targetPadding * normX),
          targetY = d.target.y - (targetPadding * normY);
      return 'M' + sourceX + ',' + sourceY + 'L' + targetX + ',' + targetY;
    });

    circle.attr('transform', function(d) {
      return 'translate(' + d.x + ',' + d.y + ')';
    });
  }
  // This function makes sure we always have the latest image of original json out there
  function recomposeJsonskel(nodes, links){
      var nd=[],ln=[];
      for (i in nodes){
          n = {id:nodes[i].id, nval:nodes[i].nval, current:nodes[i].current, total:nodes[i].total};
          nd.push(n);
      }
      for (j in links){
          li = {source:links[j].source.id, target:links[j].target.id,
            lval:links[j].lval, current:links[j].current, total:links[j].total};
          ln.push(li);
      }
      jdata = {nodes:nd,links:ln};
      return jdata
  }
  // update graph (called when needed)
  function restart() {
    // path (link) group
    path = path.data(links);

    // update existing links
    path.classed('selected', function(d) { return d === selected_link; });


    // add new links
    path.enter().append('svg:path')
      .attr('class', 'link')
      .classed('selected', function(d) { return d === selected_link; })
      .attr("stroke-width", function(d) { return Math.sqrt(d.lval); })
      .attr("color", function(d){ return colors(d.lval); })
      .on('mousedown', function(d) {
        if(d3.event.ctrlKey) return;

        // select link
        mousedown_link = d;
        if(mousedown_link === selected_link) selected_link = null;
        else selected_link = mousedown_link;
        selected_node = null;
        restart();
      });

    // remove old links
    path.exit().remove();


    // circle (node) group
    // NB: the function arg is crucial here! nodes are known by id, not by index!
    circle = circle.data(nodes, function(d) { return d.id; });

    // update existing nodes (reflexive & selected visual states)
    circle.selectAll('circle')
      .style('fill', function(d) { return (d === selected_node) ? d3.rgb(colors(d.nval)).brighter().toString() : colors(d.nval); });


    // add new nodes
    var g = circle.enter().append('svg:g');

    g.append('svg:circle')
      .attr('class', 'node')
      .attr('r', 12)
      .style('fill', function(d) { return (d === selected_node) ? d3.rgb(colors(d.nval)).brighter().toString() : colors(d.nval); })
      .style('stroke', function(d) { return d3.rgb(colors(d.nval)).darker().toString(); })
      .on('mouseover', function(d) {
        if(!mousedown_node || d === mousedown_node) return;
        // enlarge target node
        d3.select(this).attr('transform', 'scale(1.3)');

      })
      .on('mouseout', function(d) {
        if(!mousedown_node || d === mousedown_node) return;
        // unenlarge target node
        d3.select(this).attr('transform', '');
      })
      .on('mousedown', function(d) {
        if(d3.event.ctrlKey) return;

        // select node
        mousedown_node = d;
        if(mousedown_node === selected_node) selected_node = null;
        else selected_node = mousedown_node;
        selected_link = null;

        // reposition drag line
        drag_line
          .classed('hidden', false)
          .attr('d', 'M' + mousedown_node.x + ',' + mousedown_node.y + 'L' + mousedown_node.x + ',' + mousedown_node.y);

        restart();
      })
      .on('mouseup', function(d) {
        if(!mousedown_node) return;

        // needed by FF
        drag_line
          .classed('hidden', true);

        // check for drag-to-self
        mouseup_node = d;
        if(mouseup_node === mousedown_node) { resetMouseVars(); return; }

        // unenlarge target node
        d3.select(this).attr('transform', '');

        // add link to graph (update if exists)
        // NB: links are strictly source < target; arrows separately specified by booleans
        var source, target, direction;
        if(mousedown_node.id < mouseup_node.id) {
          source = mousedown_node;
          target = mouseup_node;
          //direction = 'right';
        } else {
          source = mouseup_node;
          target = mousedown_node;
          //direction = 'left';
        }

        var link;
        link = links.filter(function(l) {
          return (l.source === source && l.target === target);
        })[0];

        link = {source: source, target: target, lval:1, current:0,total:1};
        //link[direction] = true;
        links.push(link);

        // select new link
        selected_link = link;
        selected_node = null;
        restart();
      });

    // show node IDs
    g.append('svg:text')
        .attr('x', 0)
        .attr('y', 4)
        .attr('class', 'id')
        .text(function(d) { return d.id; });

    // remove old nodes
    circle.exit().remove();

    getLastNodeId();
    getLinkdata();
    getNodes();
    jsondata = recomposeJsonskel(nodes,links);
    getJsonData()
    // set the graph in motion
    force.start();
  }

  function mousedown() {
    // prevent I-bar on drag
    //d3.event.preventDefault();

    // because :active only works in WebKit?
    svg.classed('active', true);

    if(d3.event.ctrlKey || mousedown_node || mousedown_link) return;

    // insert new node at point
    function assignNodeId(lastNodeId, nodes){
      var nd=[];
      for (i in nodes){
          n = nodes[i].id;
          nd.push(n);
      }
      new_id = lastNodeId + 1
      for(j in nd){
        if(new_id == nd[j]){
          new_id = new_id + 1;
        }
      }
      return new_id;
    }
    var point = d3.mouse(this),
        node = {id: assignNodeId(lastNodeId,nodes), nval:1, current:0, total:1};
    node.x = point[0];
    node.y = point[1];
    nodes.push(node);

    restart();
  }

  function mousemove() {
    if(!mousedown_node) return;

    // update drag line
    drag_line.attr('d', 'M' + mousedown_node.x + ',' + mousedown_node.y + 'L' + d3.mouse(this)[0] + ',' + d3.mouse(this)[1]);

    restart();
  }

  function mouseup() {
    if(mousedown_node) {
      // hide drag line
      drag_line
        .classed('hidden', true)
        .style('marker-end', '');
    }

    // because :active only works in WebKit?
    svg.classed('active', false);

    // clear mouse event vars
    resetMouseVars();
  }

  function spliceLinksForNode(node) {
    var toSplice = links.filter(function(l) {
      return (l.source === node || l.target === node);
    });
    toSplice.map(function(l) {
      links.splice(links.indexOf(l), 1);
    });
  }

  // only respond once per keydown
  var lastKeyDown = -1;

  function keydown() {
    d3.event.preventDefault();

    if(lastKeyDown !== -1) return;
    lastKeyDown = d3.event.keyCode;

    // ctrl
    if(d3.event.keyCode === 17) {
      circle.call(force.drag);
      svg.classed('ctrl', true);
    }

    if(!selected_node && !selected_link) return;
    switch(d3.event.keyCode) {
      case 8: // backspace
      case 46: // delete
        if(selected_node) {
          nodes.splice(nodes.indexOf(selected_node), 1);
          spliceLinksForNode(selected_node);
        } else if(selected_link) {
          links.splice(links.indexOf(selected_link), 1);
          console.log(links)
        }
        selected_link = null;
        selected_node = null;
        restart();
        break;
      case 65: //Key A - Add
        if(selected_node){
          nodes[nodes.indexOf(selected_node)]['current'] = nodes[nodes.indexOf(selected_node)]['current'] + 1;
          nodes[nodes.indexOf(selected_node)]['total'] = nodes[nodes.indexOf(selected_node)]['total'] + 1;
          console.log(nodes.indexOf(selected_node))
        } else if(selected_link) {
          links[links.indexOf(selected_link)]['current'] = links[links.indexOf(selected_link)]['current'] + 1;
          links[links.indexOf(selected_link)]['total'] = links[links.indexOf(selected_link)]['total'] + 1;
          console.log(links.indexOf(selected_link))
        }
          restart();
          break;
      case 68: //Key D - Decrease
        if(selected_node){
          nodes[nodes.indexOf(selected_node)]['current'] = nodes[nodes.indexOf(selected_node)]['current'] - 1;
          nodes[nodes.indexOf(selected_node)]['total'] = nodes[nodes.indexOf(selected_node)]['total'] - 1;

        } else if(selected_link) {
          links[links.indexOf(selected_link)]['current'] = links[links.indexOf(selected_link)]['current'] - 1;
          links[links.indexOf(selected_link)]['total'] = links[links.indexOf(selected_link)]['total'] - 1;
        }
          restart();
          break;
    }
  }

  function keyup() {
    lastKeyDown = -1;

    // ctrl
    if(d3.event.keyCode === 17) {
      circle
        .on('mousedown.drag', null)
        .on('touchstart.drag', null);
      svg.classed('ctrl', false);
    }
  }

  // app starts here
  svg.on('mousedown', mousedown)
    .on('mousemove', mousemove)
    .on('mouseup', mouseup);
  d3.select(window)
    .on('keydown', keydown)
    .on('keyup', keyup);
  restart();

  });

}
  function returnData(){
  socket.emit('json', jsondata);
  console.log('json datadata Sent');
  }

  function updateData(){
    socket.emit('update',jsondata);
    console.log('State Update Request made');
    //svg = svg.selectAll("svg").remove();

    socket.on('graph_update', function(){
    window.location.reload(true);
    console.log('reloaded');
    });
  }
thatfunction()