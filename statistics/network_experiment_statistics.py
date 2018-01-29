import logging
import os

log = logging.getLogger(__name__)

import pandas as pd
import parse
import json

from scale_client.stats.statistics import ScaleStatistics

class NetworkExperimentStatistics(ScaleStatistics):
    """Parse the results.json-style output files from a NetworkExperiment, possibly by parsing the output files specified
    in each run.  Used to build a 'database' (really a CSV will be output) of the individual results for visualization
    of various statistics e.g. message delivery rate, latency, etc."""

    ### Helper funcs you'll likely want to override
    @property
    def varied_params(self):
        """Returns a set of the parameter names (post-extract_parameters()) that may be varied in different
        experimental treatments.  This is used predominately for average_over_runs() to know which columns we should
        group by and not drop after averaging over the 'run' column."""
        return {'error_rate', 'bw', 'delay', 'jitter', 'exp_type'}

    def collate_outputs_results(self, *results):
        """Combines the parsed results from an outputs_dir into a single DataFrame.  By default just uses _collate_results()"""
        return self.collate_results(*results)

    def get_treatment_str(self, results_filename, **params):
        """Build a string representing the experimental treatment these results come from, which is by default
        just the name of the results file without the '.json' extension or run number ('results.1.json')."""
        if results_filename.endswith('.json'):
            treatment = results_filename[:-len('.json')]
        else:
            log.warning("why does results file not end with .json???  hopefully everything parses okay...")
            treatment = results_filename

        # trim off run #, but ensure the trailing text is a run# before trimming it!
        if parse.parse('.{:d}', treatment[treatment.rfind('.'):]):
            treatment = treatment[:treatment.rfind('.')]

        return treatment

    def extract_parameters(self, exp_params):
        """
        Extracts the relevant parameters from the specified ones, possibly changing some of their names to a shorter or
        more distinct one.  Removes any parameters with null values.
        :param exp_params:
        :type exp_params: dict
        :return: dict of extracted params
        """

        exp_params['exp_type'] = exp_params.pop("experiment_type", None)
        exp_params['bw'] = exp_params.pop("bandwidth")
        # convert this one since we might calculate event-delivery latency, which would conflict as a column
        exp_params['delay'] = exp_params.pop("latency")

        # XXX: clear any null params e.g. unspecified random seeds (do this last in case anything above set None values)
        for k,v in exp_params.items():
            if v is None:
                log.debug("deleting null parameter: %s" % k)
                del exp_params[k]

        return exp_params

    # NOTE: you'll probably also need to override the choose_parser() function

    ### Helper functions that should be well-suited to most NetworkExperiments

    def extract_stats_from_results(self, results, filename, **exp_params):
        """
        With the correct ParsedSensedEvent objects (you may need to override this to choose the right parser object),
        parse the output files in the given results and return them as an aggregated DataFrame.

        :param results: 'results' json list[dict] taken directly from the results file with each dict being a run
        :type results: list[dict]
        :param filename: name of the file these results were read from: its path is used to build the actual
         path of output files that will be further parsed!
        :param exp_params:
        :return: the stats
        :rtype: pd.DataFrame
        """

        treatment = self.get_treatment_str(filename, **exp_params)

        # The outputs_dir is specified relative to the results file we're currently processing
        this_path = os.path.dirname(filename)
        dirs_to_parse = [os.path.join(this_path, run['outputs_dir']) for run in results]

        # parse each dir and combine all the parsed results into a single data frame
        stats = []
        for d in dirs_to_parse:
            o = self.parse_outputs_dir(d, treatment=treatment, **exp_params)
            stats.append(o)
        stats = self.merge_all(*stats)

        return stats

    def parse_results(self, results, filename, **params):
        """
        This version has to parse a top-level results.json-style file by parsing the individual output files from outputs_dir
        :param results:
        :type results: str
        :param filename:
        :param params:
        :return:
        """

        # this is a results.json file we're parsing at the 'top level', so we need to extract all the outputs files from it first
        assert filename in self.files

        results = json.loads(results)
        params = results['params']
        results = results['results']

        # Extract the properly-formatted results dict by combining the parameters as static data columns into the
        # results DataFrame
        params = self.extract_parameters(params)
        results = self.extract_stats_from_results(results, filename=filename, **params)

        return results

    def parse_outputs_dir(self, out_dir, treatment, **params):
        """
        Parse the individual results files in the specified outputs_dir and
        return the resulting data combined into a single pd.DataFrame.
        :param out_dir:
        :param treatment:
        :param params:
        :return:
        :rtype: pd.DataFrame
        """

        res = []
        for fname in os.listdir(out_dir):
            # need to determine how to parse this file before we can do it
            parser = self.choose_parser(fname, treatment=treatment, **params)
            fname = os.path.join(out_dir, fname)

            data = self.read_file(fname)

            # NOTE: this should include the treatment as a column in the resulting DataFrame
            data = parser(data, treatment=treatment, **params)
            res.append(data)

        # may need to override this in order to merge the different DataFrames in a particular way specific to your application
        res = self.collate_outputs_results(*res)
        return res

    def average_over_runs(self, df):
        """
        Averages the given DataFrame's values over all the runs for each unique treatment grouping.
        :type df: pd.DataFrame
        """
        # XXX: need to ensure we have all these parameters available
        cols = set(df.columns.tolist())
        group_params = list(cols.intersection(self.varied_params))

        return df.groupby(group_params).mean().reset_index().drop('run', axis=1)

    def __iadd__(self, other):
        self.stats = pd.concat((self.stats, other.stats), ignore_index=True)
        return self


if __name__ == '__main__':
    stats = NetworkExperimentStatistics.main()

    # now you can do something with the stats to e.g. get your own custom experiment results
    final_stats = stats.stats

    if stats.config.output_file:
        stats.output_stats(stats=final_stats)