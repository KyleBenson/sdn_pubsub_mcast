# @author: Kyle Benson
# (c) Kyle Benson 2018

import argparse

from network_experiment import NetworkChannelState
from scifire.defaults import *

import logging
log = logging.getLogger(__name__)


class FiredexScenario(NetworkChannelState):
    """
    Basic representation of the Sci-Fire data exchange scenario.  Used by other classes e.g. experiments, algorithms...
    Inherits NetworkChannelState to bring in parameters for bandwidth, latency, etc.
    """

    def __init__(self, num_ffs=DEFAULT_NUM_FFS, num_iots=DEFAULT_NUM_IOTS,
                 num_net_flows=DEFAULT_NUM_NET_FLOWS, num_priority_levels=DEFAULT_NUM_PRIORITIES,
                 num_topics=DEFAULT_NUM_TOPICS,
                 ### event-related distributions (can be learned from historical data in real impl.)
                 # publications
                 topic_class_weights=DEFAULT_TOPIC_CLASS_WEIGHTS, topic_class_data_sizes=DEFAULT_TOPIC_CLASS_DATA_SIZES,
                 topic_class_pub_rates=DEFAULT_TOPIC_CLASS_PUB_RATES,
                 topic_class_pub_dists=DEFAULT_TOPIC_CLASS_PUB_DISTS,
                 reliable_publication=None,
                 # subscriptions
                 topic_class_sub_dists=DEFAULT_TOPIC_CLASS_SUB_DISTS,
                 topic_class_sub_rates=DEFAULT_TOPIC_CLASS_SUB_RATES, ic_sub_rate_factor=DEFAULT_IC_SUB_RATE_FACTOR,
                 topic_class_sub_start_times=DEFAULT_TOPIC_CLASS_SUB_START_TIMES,
                 topic_class_sub_durations=DEFAULT_TOPIC_CLASS_SUB_DURATIONS,
                 # TODO: utilities?
                 # XXX: for multiple inheritance
                 **kwargs):
        """
        :param num_ffs:
        :param num_iots:
        :param num_net_flows:
        :param num_priority_levels:
        :param num_topics:
        :param topic_class_weights:
        :param topic_class_data_sizes:
        :param topic_class_pub_rates:
        :param topic_class_pub_dists:
        :param reliable_publication:
        :param topic_class_sub_dists:
        :param topic_class_sub_rates:
        :param ic_sub_rate_factor:
        :param topic_class_sub_start_times:
        :param topic_class_sub_durations:
        :param kwargs:
        """

        # XXX: ensure we call all the super constructors!
        try:
            super(FiredexScenario, self).__init__(**kwargs)
        except TypeError:
            super(FiredexScenario, self).__init__()

        self.num_ffs = num_ffs
        self.num_iots = num_iots
        self.num_net_flows = num_net_flows
        self.num_priority_levels = num_priority_levels
        self.num_topics = num_topics

        # publication-related params
        self.topic_class_weights = topic_class_weights
        self._ntopics_per_class = None  # filled in later according to the above weights
        self.topic_class_data_sizes = topic_class_data_sizes
        self.topic_class_pub_rates = topic_class_pub_rates
        self.topic_class_pub_dists = topic_class_pub_dists

        # TODO: implement this eventually
        self.reliable_publication = reliable_publication
        if self.reliable_publication is not None and self.reliable_publication:
            log.warning("reliable publications not currently implemented!")

        # subscription-related params
        self.topic_class_sub_dists = topic_class_sub_dists
        self.topic_class_sub_rates = topic_class_sub_rates
        self.ic_sub_rate_factor = ic_sub_rate_factor
        # TODO: manipulate distributions with default args according to e.g. num_topics
        # self.topic_subscription_dist['scale'] = self.num_topics
        self.topic_class_sub_start_times = topic_class_sub_start_times
        self.topic_class_sub_durations = topic_class_sub_durations

        # Validate input params:
        # First, verify consistent # topic classes and record a value to make sure we don't have mis-matched lens later
        ntopic_classes = [len(self.topic_class_weights), len(self.topic_class_data_sizes), len(topic_class_pub_rates),
                          len(self.topic_class_sub_dists)]
        self.ntopic_classes = ntopic_classes[0]
        if not all(x == self.ntopic_classes for x in ntopic_classes):
            self.ntopic_classes = min(ntopic_classes)
            log.info("inconsistent sizes for topic class parameters!  Assuming minimum # topics classes: %d" % self.ntopic_classes)

    def as_dict(self):
        """
        Puts all relevant parameters into a dictionary for e.g. serialization, passing to other processes, etc.
        :return:
        """
        # NOTE: we shorten these names for easy viewing in e.g. spreadsheet format
        return {'nffs': self.num_ffs,
                'niots': self.num_iots,
                'nflows': self.num_net_flows,
                'nprios': self.num_priority_levels,
                # everything topics / publications-related
                'ntopics': self.num_topics,
                'tc_weights': self.topic_class_weights,
                'tc_sizes': self.topic_class_data_sizes,
                'tc_pub_rates': self.topic_class_pub_rates,
                'tc_pub_dists': self.topic_class_pub_rates,
                'retx_pubs': self.reliable_publication,
                # subscriptions
                'tc_sub_rates': self.topic_class_sub_rates,
                'tc_sub_dists': self.topic_class_sub_dists,
                'ic_subs': self.ic_sub_rate_factor,
                'tc_sub_start': self.topic_class_sub_start_times,
                'tc_sub_dur': self.topic_class_sub_durations,
                }

    @property
    def topics(self):
        """All topics across all classes"""
        for c in self.topic_classes:
            for t in c:
                yield t

    @property
    def topic_classes(self):
        """Iterable of topic classes, where each class is an iterable of topics"""

        for i, l in enumerate(self.ntopics_per_class):
            yield self.topics_for_class(i)

    @property
    def ntopics_per_class(self):
        """
        :rtype: iterable[int]
        :return:
        """
        if self._ntopics_per_class is None:
            assert sum(self.topic_class_weights) <= 1.0
            # assign topic classes according to the specified weights in an exact (non-random) manner (helps repeatability)
            self._ntopics_per_class = [int(w * self.num_topics) for w in self.topic_class_weights]
            # add extra topics due to round-off error or sub-1.0 class weights to the first class
            unassigned_topics = self.num_topics - sum(self._ntopics_per_class)
            self._ntopics_per_class[0] += unassigned_topics
        return self._ntopics_per_class

    # TODO: add a prefix option to generate strings?
    def topics_for_class(self, class_idx):
        """Iterable of topics for the specified class index"""
        prev_topics = sum(self.ntopics_per_class[:class_idx])
        return xrange(prev_topics, prev_topics + self.ntopics_per_class[class_idx])

    @classmethod
    def get_arg_parser(cls, parents=(), add_help=False):
        """
        Argument parser that can be combined with others when this class is used in a script.
        Need to not add help options to use that feature, though.
        :param tuple[argparse.ArgumentParser] parents:
        :param add_help: if True, adds help command (set to False if using this arg_parser as a parent)
        :return argparse.ArgumentParser arg_parser:
        """

        arg_parser = argparse.ArgumentParser(parents=parents, add_help=add_help, conflict_handler='resolve')

        # experimental treatment parameters
        arg_parser.add_argument('--num-ffs', '-nf', dest='num_ffs', type=int, default=DEFAULT_NUM_FFS,
                                help='''The number of fire fighter 'hosts' to create, which represent a FF equipped with
                                IoT devices that relay their data through some wireless smart hub (default=%(default)s).''')
        arg_parser.add_argument('--num-iots', '-nd', '-ni', dest='num_iots', type=int, default=DEFAULT_NUM_IOTS,
                                help='''The number of IoT device hosts to create, which represent various sensors,
                                actuators, or other IoT devices that reside within the building and publish
                                fire event-related data to the BMS (default=%(default)s).''')
        arg_parser.add_argument('--num-net-flows', '-nn', dest='num_net_flows', type=int, default=DEFAULT_NUM_NET_FLOWS,
                                help='''The number of distinct network flows (per host) to consider, which will
                                correspond to the number of distinct pub-sub client connections to open up.  Each flow
                                is mapped to a priority level to prioritize the topics transmitted on that connection
                                (default=%(default)s).''')
        arg_parser.add_argument('--num-priorities', '-np', '-nq', dest='num_priority_levels', type=int, default=DEFAULT_NUM_PRIORITIES,
                                help='''The number of priority levels to consider, which will correspond to the number
                                 of priority queues configured in the network (default=%(default)s).''')
        arg_parser.add_argument('--num-topics', '-nt', dest='num_topics', type=int, default=DEFAULT_NUM_TOPICS,
                                help='''The number of event topics to consider, which will be mapped to network flows
                                 for prioritization by the network (default=%(default)s).''')

        # TODO: topic-generation models: who is interested? what's their utility? data rate needed? periodic vs. async?
        # TODO: utility function?
        # TODO: event-generation models (data requests, network changes, and sensed events) and random seeds?
        # TODO: how to bring in network-related parameters?  Maybe make a NetworkScenario base class?
        # TODO: some notion of time?

        return arg_parser
