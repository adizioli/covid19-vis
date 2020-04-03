from __future__ import annotations
from typing import Union

import numpy as np
import pandas as pd

from .chart_spec import ChartSpec
from .start_criterion import StartCriterion, DaysSinceNumReached
from .utils import days_between


class CovidChart(object):
    """
    A class that composes a ChartSpec and uses the state therein to compute a dataframe
    that will be used as input to altair to generate a Vega-Lite spec. None of the altair
    stuff happens here (that's in ChartSpec), but *all* of the dataframe processing happens here.

    Also provides various convenience methods for setting ChartSpec state.
    """
    lockdown_X = 'lockdown_x'
    X = 'x'
    Y = 'y'

    def __init__(
        self,
        df: Union[str, pd.DataFrame],
        groupcol: str,
        start_criterion: StartCriterion,
        ycol: str,
        use_defaults: bool = True,
        ycol_is_cumulative: bool = True,
        top_k_groups: int = None,
        xcol: str = 'date',
        quarantine_df: pd.DataFrame = None,
    ):
        if isinstance(df, str):
            df = pd.read_csv(df, parse_dates=[xcol], infer_datetime_format=True)
        if groupcol not in df.columns:
            raise ValueError('grouping col should be in dataframe cols')
        if ycol not in df.columns:
            raise ValueError('measure col should be in dataframe cols')

        if quarantine_df is not None:
            if groupcol not in quarantine_df.columns:
                raise ValueError('grouping col should be in dataframe cols')
            if 'lockdown_date' not in quarantine_df.columns:
                raise ValueError('lockdown_date should be in quarantine_df columns')
            if 'lockdown_type' not in quarantine_df.columns:
                raise ValueError('lockdown_type should be in quarantine_df columns')

        self.df = df
        self.quarantine_df = quarantine_df
        self.groupcol = groupcol
        self.start_criterion = start_criterion
        self.xcol = xcol
        self.ycol = ycol
        self.ycol_is_cumulative = ycol_is_cumulative
        self.top_k_groups = top_k_groups
        self.spec = ChartSpec()
        self.spec.detailby = groupcol
        self.spec.colorby = groupcol
        self.spec.facetby = None
        if use_defaults:
            self.set_defaults()

    def _preprocess_df(self) -> pd.DataFrame:
        df = self.df.copy()
        if self.ycol_is_cumulative:
            df[self.Y] = df[self.ycol]
        else:
            df[self.Y] = np.zeros_like(df[self.ycol])
            for group in df[self.groupcol].unique():
                pred = df[self.groupcol] == group
                df.loc[pred, self.Y] = df.loc[pred, self.ycol].cumsum()

        if self.top_k_groups is not None:
            top_k_groups = list(df.groupby(self.groupcol)[self.Y].max().nlargest(self.top_k_groups).index)
            df = df.loc[df[self.groupcol].isin(top_k_groups)]

        df = self.start_criterion.transform(self, df)
        if self.quarantine_df is not None:
            quarantine_df = self.quarantine_df.copy()
            quarantine_df = quarantine_df.dropna()
            quarantine_df = quarantine_df.merge(
                df[[self.groupcol, 'date_of_N']], on=self.groupcol, how='inner'
            )
            quarantine_df[self.lockdown_X] = quarantine_df.apply(lambda x: days_between(x['date_of_N'], x['lockdown_date']), axis=1)

            # only retain earliest lockdown that appears... eventually we will want to allow for multiple
            quarantine_df = quarantine_df.loc[quarantine_df.lockdown_x > 0]
            quarantine_df = quarantine_df.loc[quarantine_df.groupby(self.groupcol).lockdown_x.idxmin()]
            del quarantine_df['date_of_N']
            df = df.merge(quarantine_df, how='left', on=self.groupcol)

            idx_before_at_lockdown = df.loc[df.x <= df.lockdown_x].groupby(df[self.groupcol]).x.idxmax()
            df_lockdown_y = df.loc[idx_before_at_lockdown]
            df_intercept = df.loc[df.x == 0].groupby(self.groupcol).first().reset_index()
            df = df.merge(
                df_intercept.rename(columns={'y': 'intercept'})[[self.groupcol, 'intercept']],
                how='left',
                on=self.groupcol
            )
            df = df.merge(
                df_lockdown_y.rename(columns={'y': 'lockdown_y'})[[self.groupcol, 'lockdown_y']],
                how='left',
                on=self.groupcol
            )
            df['lockdown_slope'] = np.exp(np.log(df.lockdown_y / df.intercept) / df.lockdown_x)

            # these new rows are to ensure we have at least one point where x == lockdown_x since this is the filter
            # used to generate lockdown rules...
            # we need this b/c we can only attach mouseover interactions to one column, and it is already attached to x

            # TODO (smacke): instead of x and lockdown_x, we should have x and x_type, where x_type can be normal,
            # lockdown, etc... This will also generalize better if we want to change x based on e.g. a dropdown
            new_rows = df.groupby(self.groupcol).max().reset_index()[[self.groupcol, self.lockdown_X, 'lockdown_type']]
            new_rows['x'] = new_rows.lockdown_x
            df = df.append(new_rows, ignore_index=True, sort=False)

        if 'xdomain' in self.spec:
            xmin, xmax = self.spec.xdomain[0], self.spec.xdomain[1]
            df = df.loc[(df[self.X] >= xmin) & (df[self.X] <= xmax)]
        if 'ydomain' in self.spec:
            ymin, ymax = self.spec.ydomain[0], self.spec.ydomain[1]
            df = df.loc[(df[self.Y] >= ymin) & (df[self.Y] <= ymax)]
        return df

    def _make_info_dict(self, qdf):
        info_dict = {}

        def _make_info_from_row(row):
            info_dict[row[self.groupcol]] = f'{self.groupcol} is implementing a general lockdown '
            f'{"across the territory" if row["Planned end date"] is None else "in specific regions"}. '
            f'The lockdown started on {row["DateEnacted"]}. '
            f'{"No specific end date is announced" if row["Planned end date"] is None else "It will last until {}".format(row["Planned end date"])}.'

        qdf.apply(_make_info_from_row)
        return info_dict

    def set_title(self, title):
        self.spec.title = title
        return self
        
    def set_logscale(self):
        self.spec.yscale = 'log'
        return self

    def set_xdomain(self, limits):
        self.spec.xdomain = limits
        return self

    def set_ydomain(self, limits):
        self.spec.ydomain = limits
        return self

    def set_xtitle(self, xtitle):
        self.spec.xtitle = xtitle
        return self

    def set_ytitle(self, ytitle):
        self.spec.ytitle = ytitle
        return self

    def add_lines(self):
        self.spec.lines = True
        return self

    def add_points(self):
        self.spec.points = True
        return self

    def set_click_selection(self):
        self.spec.click_selection = True
        return self

    def set_legend_selection(self):
        self.spec.legend_selection = True
        return self

    def add_tooltip_text(self):
        self.spec.has_tooltips = True
        self.spec.tooltip_text = True
        return self

    def add_tooltip_points(self):
        self.spec.has_tooltips = True
        self.spec.tooltip_points = True
        return self

    def add_tooltip_rules(self):
        self.spec.has_tooltips = True
        self.spec.tooltip_rules = True
        return self

    def add_lockdown_rules(self):
        self.spec.has_tooltips = True
        self.spec.lockdown_rules = True
        return self

    def set_height(self, height):
        self.spec.height = height
        return self

    def set_width(self, width):
        self.spec.width = width
        return self

    def add_all_tooltips(self):
        return self.add_tooltip_points().add_tooltip_text().add_tooltip_rules()

    def add_lockdown_extrapolation(self):
        self.spec.lockdown_extrapolation = True
        return self

    def set_interactive(self, interactive=True):
        self.spec.interactive = interactive
        return self

    def colorby(self, col):
        self.spec.colorby = col
        return self

    def facetby(self, col):
        self.spec.facetby = col
        return self

    def set_point_size(self, point_size):
        self.spec.point_size = point_size
        return self

    def set_defaults(self):
        self.spec.detailby = self.groupcol
        self.spec.colorby = self.groupcol
        self.spec.point_size = ChartSpec.DEFAULT_POINT_SIZE
        ret = self.add_lines(
        ).add_points(
        ).set_logscale(
        ).set_click_selection(
        ).set_legend_selection(
        ).add_all_tooltips(
        ).add_lockdown_extrapolation(
        ).set_interactive(False).set_width(
            self.spec.DEFAULT_WIDTH
        ).set_height(
            self.spec.DEFAULT_HEIGHT
        )
        if self.quarantine_df is not None:
            ret = ret.add_lockdown_rules()
        return ret

    def compile(self):
        chart_df = self._preprocess_df()
        return self.spec.compile(chart_df)

    def export(self, fname="vis.json", js_var="vis"):
        import json
        with open(fname, 'w') as f:
            f.write(f"var {js_var} = {json.dumps(self.compile().to_dict())}")