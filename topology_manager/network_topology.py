import logging as log
import math

import networkx as nx
import dsm_networkx_algorithms as dsm_algs
import json
from networkx.readwrite import json_graph


class NetworkTopology(object):
    """Uses networkx graph model and various algorithms to perform
    various networking-related computations such as paths, multicast
    trees, and redundant (reliable) variations of them.  This is where
    you should implement generic graph algorithms for use in the
    other SdnTopology classes."""

    def __init__(self, topo=None):
        """
        :type topo: nx.Graph
        """
        super(NetworkTopology, self).__init__()
        if topo is None:
            self.topo = nx.Graph()
        else:
            self.topo = topo

    def load_from_file(self, filename):
        with open(filename) as f:
            data = json.load(f)
        self.topo = json_graph.node_link_graph(data)


    def get_redundant_multicast_trees(self, source, destinations, k=2, algorithm='steiner',
                                      weight_metric='weight', heur_args=None):
        """Builds k redundant multicast trees: trees should not share any edges
        unless necessary.  Supports various algorithms, several of which may not
        work for k>2."""

        # Need to sanitize the input to ensure that we know about all of the given
        # destinations or else we'll cause an exception.
        old_dests = destinations
        destinations = []
        for d in old_dests:
            if d not in self.topo:
                log.warning("Skipping unknown destination %s in requested multicast tree" % d)
            else:
                destinations.append(d)

        if algorithm == 'steiner':
            """Default algorithm implemented by networkx that uses sum of
            shortest paths 2*D approximation.  Currently not available in
            latest release of networkx, so see README if this import doesn't work."""

            try:
                from networkx.algorithms.approximation import steiner_tree
            except ImportError:
                raise NotImplementedError("Steiner Tree algorithm not found!  See README")

            # we don't care about directionality of the mcast tree here,
            # so we can treat the source as yet another destination
            destinations = destinations + [source]

            # Skip over graph modifications if we only want one tree
            if k == 1:
                return [steiner_tree(self.topo, destinations)]

            # Naive heuristic: generate a multicast tree, increase the
            # weights on the edges to discourage them, generate another...
            # So we need to add a temporary attribute to the edges for
            # the heuristic to use or else we'd overwrite the weights.
            # TODO: generalize this residual graph approach?

            for u,v in self.topo.edges():
                self.topo[u][v]['_temp_mcast_weight'] = self.topo[u][v].get(weight_metric, 1.0)
            # Disjoint trees heuristic: we have the choice of two penalties that we
            # add to an edge's weight to prevent it from being chosen next round:
            # 1) args[0] == 'max' --> the max weight of all edges
            # 2) args[0] == 'double' --> double the weight of the edge
            penalty_heuristic = 'max'
            if heur_args is not None and len(heur_args) >= 1:
                if heur_args[0] not in ('max', 'double'):
                    log.warn("Unknown steiner tree edge penalty heuristic (args[0]): %s. Using max instead" % heur_args[0])
                else:
                    penalty_heuristic = heur_args[0]

            max_weight = max((e[2]['_temp_mcast_weight'] for e in self.topo.edges(data=True)))

            trees = []
            for i in range(k):
                new_tree = steiner_tree(self.topo, destinations, weight='_temp_mcast_weight')
                for u,v in new_tree.edges():
                    if penalty_heuristic == 'max':
                        self.topo[u][v]['_temp_mcast_weight'] += max_weight
                    else:  # must be double
                        self.topo[u][v]['_temp_mcast_weight'] *= 2
                trees.append(new_tree)

            for u,v in self.topo.edges():
                del self.topo[u][v]['_temp_mcast_weight']
            results = trees

        elif algorithm == 'diverse-paths':
            """This algorithm builds multiple trees by getting multiple paths
            to each terminal (destination) and selectively adding these paths
            together to create each tree. The heuristic chooses destinations
            in increasing order of shortest path from source. It adds a given
            path to the tree with the most components in common so as to
            create somewhat minimally-sized multicast trees."""

            destinations = set(destinations)
            shortest_paths = nx.shortest_path_length(self.topo, source, weight=weight_metric)
            shortest_paths = ((l, d) for d, l in shortest_paths if d in destinations)
            sorted_destinations = sorted(shortest_paths)

            # Track trees as sets of edges to make checking overlap faster
            # NOTE: if the path overlaps with the tree in terms of a node
            # but not an edge incident with that node, we have a cycle!
            trees = [set() for i in range(k)]

            for _, d in sorted_destinations:
                paths = self.get_redundant_paths(source, d, k)
                # ensure each tree receives a path
                trees_left = set(range(k))
                for i, p in enumerate(paths):
                    # Add this path to the tree with most components in common
                    edges = self.get_edges_for_path(p)
                    overlaps = ((len(trees[j].intersection(edges)), j) for j in trees_left)
                    best_tree = max(overlaps)[1]
                    trees_left.remove(best_tree)
                    trees[best_tree].update(edges)

            # Subgraph the topology with the trees' edges to maintain attributes
            results = [self.topo.edge_subgraph(t) for t in trees]
            # Sanity check that we're generating actual trees
            for i, t in enumerate(results):
                # If it isn't a tree for some reason, trim it down until it is
                # by first getting a spanning tree of it and then trimming off
                # any leaf nodes that aren't terminals (destinations).
                if not nx.is_tree(t):
                    log.info("non-tree mcast tree generated!")
                    new_t = t.edge_subgraph(nx.minimum_spanning_edges(t, data=False, weight=weight_metric))
                    non_terminal_leaves = [n for n in new_t.nodes() if\
                                           (new_t.degree(n) == 1 and n not in destinations and n != source)]
                    while len(non_terminal_leaves) > 0:
                        log.info("trimming tree leaves: %s" % non_terminal_leaves)
                        new_t.remove_nodes_from(non_terminal_leaves)
                        non_terminal_leaves = [n for n in new_t.nodes() if\
                                               (new_t.degree(n) == 1 and n not in destinations and n != source)]
                    results[i] = new_t

        elif algorithm == 'red-blue':
            """SkeletonList red-blue paths construction based off
            2013 Bejerano and Koppol (Bell Labs) paper entitled
            'Link-Coloring Based Scheme for Multicast and Unicast Protection'.
            This only gives us two redundant yet maximally disjoint subgraphs
            so we need to apply some other heuristic to further partition them.

            Currently, we recursively apply this procedure to the pair of graphs
            in order to generate a number of maximally disjoint trees numbering
            a power of 2"""

            # TODO: determine how to better support k not powers of 2
            if k != (2**int(math.log(k, 2))):
                log.warn("Requested %d redundant red-blue trees, but we currently only fully support powers of 2 for k!  Slicing off tail end of results..." % k)

            from redundant_multicast_algorithms import SkeletonList

            # Repeatedly apply the procedure over everything currently in the results,
            # which doubles the number of maximally disjoint spanning DAGs each time
            results = [self.topo]

            for i in range(int(math.ceil(math.log(k, 2)))):
                this_round = []
                for t in results:
                    sl = SkeletonList(t, source)
                    red_dag = sl.get_red_graph()
                    blue_dag = sl.get_blue_graph()
                    this_round.append(red_dag)
                    this_round.append(blue_dag)
                results = this_round
            assert len(results) >= k

            # Now we need to turn the results into multicast trees
            try:
                from networkx.algorithms.approximation import steiner_tree
            except ImportError:
                raise NotImplementedError("Steiner Tree algorithm not found!  See README")

            assert all(all(d in g for d in destinations) for g in results)

            # Slice off unrequested results, and then convert to undirected
            # graphs
            results = results[:k]
            results = [steiner_tree(t, destinations, root=source, weight=weight_metric).to_undirected() for t in results]
            assert not any(r.is_directed() for r in results)

        elif algorithm == 'ilp':
            """Our (UCI-DSM group) proposed ILP-based heuristic."""
            from redundant_multicast_algorithms import ilp_redundant_multicast
            results = ilp_redundant_multicast(self.topo, source, destinations, k)

        else:
            raise ValueError("Unkown multicast tree generation algorithm %s" % algorithm)

        # Finally, we need to make a new graph copy for each of the trees since we used
        # subgraph to build them, which means they share the same 'graph' object, which
        # means they will overwrite each other's attributes when doing e.g. g.graph['address'] = ip_addr
        results = [nx.Graph(t) for t in results]

        # Some sanity checks to verify that they're all trees and all subscribers are reachable from the root (connected)
        if __debug__:
            for tree in results:
                if not nx.is_tree(tree):
                    log.warn("Non-tree multicast tree generated by %s algorithm!" % algorithm)
                    log.debug("Edges are: %s" % list(tree.edges()))
                if not nx.is_connected(tree):
                    log.warn("disconnected multicast tree generated by %s algorithm!" % algorithm)
                    log.debug("Edges are: %s" % list(tree.edges()))
                    log.debug("Nodes are: %s" % list(tree.nodes()))

        return results

    def get_multicast_tree(self, source, destinations, algorithm='steiner'):
        """Uses networkx algorithms to build a multicast tree for the given source node and
        destinations (an iterable).  Can be used to build and install flow rules.
        Current implementation simply calls to get_redundant_multicast_trees(k=1)
        Default algorithm uses the metric closure-based approximation of a steiner tree."""

        return self.get_redundant_multicast_trees(source, destinations, 1, algorithm)[0]

    # Path generation procedures

    def get_redundant_paths(self, source, destination, k=2):
        """Gets k (possibly shortest) redundant paths with minimal component overlap.
        Current version based on Zheng et al 2010 paper entitled
        'Minimum-Cost Multiple Paths Subject to Minimum Link and Node Sharing in a Network'.
        The basic idea is to use network flow on a modified graph where each edge can
        handle one flow at regular cost but any others have greatly increased cost.
        This implementation assumes we only care about min-sum costs of edges then nodes
        for the constraints. Running time = O(k(E+VlogV))"""

        return dsm_algs.get_redundant_paths(self.topo, source, destination, k)

    def get_multi_source_disjoint_paths(self, sources, target, weight='weight'):
        """Returns disjoint (possibly shortest) paths from each source to the target."""
        return dsm_algs.get_multi_source_disjoint_paths(self.topo, sources, target, weight=weight)

    def get_path(self, source, destination, weight='weight'):
        """Gets shortest path by the optionally specified weight attribute between the nodes.
        @:return a sequence of nodes representing the shortest path"""

        return nx.shortest_path(self.topo, source=source, target=destination, weight=weight)

    @staticmethod
    def merge_paths(path1, path2):
        """Merges the two specified paths, which are formatted as returned by get_path()"""

        return dsm_algs.merge_paths(path1, path2)

    @staticmethod
    def get_edges_for_path(p):
        """
        Returns the edges in path p using zip
        :param p: a path expressed as an ordered list of nodes
        :type p: list
        :return: ordered list of (src, dst) pairs
        """
        return dsm_algs.get_edges_for_path(p)

    def draw(self):
        """Draws the topology"""
        # XXX: just defer to another draw method for now by specifying empty overlay params
        self.draw_paths([])

    def draw_multicast_trees(self, trees):
        """Draws the trees as graphs overlaid on the original topology"""
        dsm_algs.draw_overlaid_graphs(self.topo, trees)

    def draw_paths(self, paths):
        """Draws the given paths overlaid on the original topology graph"""
        dsm_algs.draw_paths(self.topo, paths)


# Run various tests
if __name__ == '__main__':
    algorithm = 'diverse-paths'
    # algorithm = 'ilp'
    ntrees = 3
    from_file = False
    draw_trees = True

    log.basicConfig(format='%(levelname)s:%(message)s', level=log.DEBUG)

    if from_file:
        net = NetworkTopology()
        source = "s0"
        net.load_from_file('campus_topo.json')
        # dest = ["h1-b4", "h2-b7", "h3-b0", "h2-b0", "h4-b2", "h5-b21", "h6-b45", "h7-b71"]
        dest = ["h1-b4", "h2-b5", "h3-b0"]
    else:
        g = nx.complete_graph(4)
        g.add_edge(0, "s")
        g.add_edge(3, "d1")
        g.add_edge(2, "d1")
        g.add_edge(1, "d2")
        # Need to relabel to strings since we assume nodes are strings
        nx.relabel_nodes(g, {i: str(i) for i in g.nodes()}, copy=False)
        net = NetworkTopology(g)

        dest = ["d1", "d2"]
        source = "s"

    # First, test out our added path functions
    p1 = net.get_path(source, dest[0])
    p2 = net.get_path(dest[0], dest[1])
    pm = net.merge_paths(p1, p2)

    # Verify it by ensuring each edge is in the topo as well as one of the first paths
    pm_edges = net.get_edges_for_path(pm)
    p1_edges = net.get_edges_for_path(p1)
    p2_edges = net.get_edges_for_path(p2)
    assert len(pm) > 1, "merged path is too small for meaningful tests!"
    assert len(pm_edges) == len(pm) - 1  # right # edges?
    for i,j in pm_edges:
        assert net.topo.has_edge(i, j), "edge from merged path not present in original topology!"
        assert (i,j) in p1_edges or (i,j) in p2_edges, "edge from merged path not present in either original path!"

    # Test some simple cases: empty paths and error conditions
    assert net.merge_paths([], p2) == p2
    assert net.merge_paths(p1, []) == p1
    assert net.merge_paths([], []) == []
    try:
        net.merge_paths([1,2,3], [4,5,6])
        assert False, "merging paths without a join point should cause an error!"
    except ValueError:
        pass

    # Now, test our multicast tree functions
    M = net.get_redundant_multicast_trees(source, dest, ntrees, algorithm)

    if draw_trees:
        net.draw_multicast_trees(M)

    # Test out draw_paths feature
    # p1 = ["s0", "c0", "b0", "h0-b0"]
    # p2 = ["s0", "c0", "b4", "h0-b4"]
    # net.draw_paths([p1, p2])