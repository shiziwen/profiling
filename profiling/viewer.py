# -*- coding: utf-8 -*-
"""
    profiling.viewer
    ~~~~~~~~~~~~~~~~
"""
from __future__ import absolute_import
import os

import urwid
from urwid import connect_signal as on

from .sortkeys import by_total_time


class Formatter(object):

    def _markup(get_string, get_attr=None):
        def markup(self, value):
            string = get_string(self, value)
            if get_attr is None:
                return string
            attr = get_attr(self, value)
            return (attr, string)
        return markup

    _numeric = {'align': 'right', 'wrap': 'clip'}

    def _make_text(get_markup, **kwargs):
        def make_text(self, value):
            markup = get_markup(self, value)
            return urwid.Text(markup, **kwargs)
        return make_text

    # percent

    def format_percent(self, ratio):
        if ratio >= 1:
            precision = 0
        elif ratio >= 0.1:
            precision = 1
        else:
            precision = 2
        return ('{:.' + str(precision) + '%}').format(ratio)

    def attr_ratio(self, ratio):
        if ratio > 0.9:
            return 'danger'
        elif ratio > 0.7:
            return 'caution'
        elif ratio > 0.3:
            return 'warning'
        elif ratio > 0.1:
            return 'notice'
        elif ratio <= 0:
            return 'zero'

    markup_percent = _markup(format_percent, attr_ratio)
    make_percent_text = _make_text(markup_percent, **_numeric)

    # int

    def format_int(self, num):
        return '{:.0f}'.format(num)

    def attr_int(self, num):
        return None if num else 'zero'

    markup_int = _markup(format_int, attr_int)
    make_int_text = _make_text(markup_int, **_numeric)

    # clock

    def format_clock(self, sec):
        if sec == 0:
            return '0'
        elif sec < 1:
            return '{:,.0f}'.format(sec * 1e6).replace(',', '.')
        else:
            return '{:.2f}s'.format(sec)

    def attr_clock(self, sec):
        if sec == 0:
            return 'zero'
        elif sec < 1:
            return 'usec'
        else:
            return 'sec'

    markup_clock = _markup(format_clock, attr_clock)
    make_clock_text = _make_text(markup_clock, **_numeric)

    # stat

    def markup_stat(self, stat):
        if stat.name and stat.module:
            return [('name', stat.name), ('module', ' (' + stat.module + ')')]
        elif not stat.name and not stat.module:
            return ('module', stat.filename)  # e.g. <string>
        else:
            return ('module', stat.name or stat.module)

    make_stat_text = _make_text(markup_stat, wrap='clip')

    del _markup
    del _make_text


fmt = Formatter()


class StatWidget(urwid.TreeWidget):

    signals = ['expanded', 'collapsed']
    icon_chars = ('+', '-', ' ')  # collapsed, expanded, leaf

    def __init__(self, node):
        super(StatWidget, self).__init__(node)
        self._w = urwid.AttrWrap(self._w, None, StatisticsViewer.focus_map)
        self.collapse()

    @property
    def expanded(self):
        return self._expanded

    @expanded.setter
    def expanded(self, expanded):
        in_init = not hasattr(self, 'expanded')
        self._expanded = expanded
        if in_init:
            return
        if expanded:
            urwid.emit_signal(self, 'expanded')
        else:
            urwid.emit_signal(self, 'collapsed')

    def selectable(self):
        return True

    def load_inner_widget(self):
        node = self.get_node()
        stat = node.get_value()
        stats = node.get_root().get_value()
        return StatisticsTable.make_columns([
            fmt.make_stat_text(stat),
            fmt.make_percent_text(stat.total_time / stats.cpu_time),
            fmt.make_percent_text(stat.own_time / stats.cpu_time),
            fmt.make_int_text(stat.calls),
            fmt.make_clock_text(stat.total_time),
            fmt.make_clock_text(stat.total_time_per_call),
            fmt.make_clock_text(stat.own_time),
            fmt.make_clock_text(stat.own_time_per_call)])

    def get_indented_widget(self):
        icon = self.get_mark()
        widget = self.get_inner_widget()
        widget = urwid.Columns([('fixed', 1, icon), widget], 1)
        indent = (self.get_node().get_depth() - 1)
        widget = urwid.Padding(widget, left=indent)
        return widget

    def get_mark(self):
        if self.is_leaf:
            char = self.icon_chars[2]
        else:
            char = self.icon_chars[int(self.expanded)]
        return urwid.SelectableIcon(('mark', char), 0)

    def update_mark(self):
        widget = self._w.base_widget
        try:
            widget.widget_list[0] = self.get_mark()
        except (AttributeError, TypeError):
            pass

    def update_expanded_icon(self):
        self.update_mark()

    def expand(self):
        self.expanded = True
        self.update_mark()

    def collapse(self):
        self.expanded = False
        self.update_mark()

    def keypress(self, size, key):
        command = self._command_map[key]
        if command == urwid.ACTIVATE:
            key = '-' if self.expanded else '+'
        elif command == urwid.CURSOR_RIGHT:
            key = '+'
        elif self.expanded and command == urwid.CURSOR_LEFT:
            key = '-'
        return super(StatWidget, self).keypress(size, key)

    def mouse_event(self, *args, **kwargs):
        pass


class EmptyWidget(urwid.Widget):
    """A widget which doesn't render anything."""

    def __init__(self, rows=0):
        super(EmptyWidget, self).__init__()
        self._rows = rows

    def rows(self, size, focus=False):
        return self._rows

    def render(self, size, focus=False):
        return urwid.SolidCanvas(' ', size[0], self.rows(size, focus))


class StatisticsWidget(StatWidget):

    def load_inner_widget(self):
        return EmptyWidget()

    def get_indented_widget(self):
        return self.get_inner_widget()

    def get_mark(self):
        raise TypeError('Statistics widget has no mark')

    def update(self):
        pass

    def unexpand(self):
        pass


class StatNodeBase(urwid.TreeNode):

    def __init__(self, stat=None, parent=None, key=None, depth=None,
                 table=None):
        super(StatNodeBase, self).__init__(stat, parent, key, depth)
        self.table = table

    def get_focus(self):
        widget, focus = super(StatNodeBase, self).get_focus()
        if self.table is not None:
            self.table.walker.set_focus(self)
        return widget, focus

    def get_widget(self, reload=False):
        if self._widget is None or reload:
            self._widget = self.load_widget()
            self.setup_widget(self._widget)
        return self._widget

    def load_widget(self):
        return self._widget_class(self)

    def setup_widget(self, widget):
        if self.table is None:
            return
        stat_token = self.table.tokenize_stat(self.get_value())
        if stat_token in self.table._expanded_stat_tokens:
            widget.expand()


class NullStatWidget(StatWidget):

    def get_indented_widget(self):
        return urwid.Text(('weak', 'Not Available'))


class NullStatNode(StatNodeBase):

    _widget_class = NullStatWidget


class LeafStatNode(StatNodeBase):

    _widget_class = StatWidget


class StatNode(StatNodeBase, urwid.ParentNode):

    def total_usage(self):
        stat = self.get_value()
        stats = self.get_root().get_value()
        try:
            return stat.total_time / stats.cpu_time
        except AttributeError:
            return 0.0

    def load_widget(self):
        if self.is_root():
            widget_class = StatisticsWidget
        else:
            widget_class = StatWidget
        return widget_class(self)

    def setup_widget(self, widget):
        super(StatNode, self).setup_widget(widget)
        if self.get_depth() == 0:
            # just expand the root node
            widget.expand()
            return
        table = self.table
        if table is None:
            return
        on(widget, 'expanded', table._widget_expanded, widget)
        on(widget, 'collapsed', table._widget_collapsed, widget)

    def load_child_keys(self):
        stat = self.get_value()
        if stat is None:
            return ()
        return stat.sorted(self.table.order)

    def load_child_node(self, stat):
        depth = self.get_depth() + 1
        node_class = StatNode if len(stat) else LeafStatNode
        return node_class(stat, self, stat, depth, self.table)


class StatisticsListBox(urwid.TreeListBox):

    signals = ['focus_changed']

    def change_focus(self, *args, **kwargs):
        super(StatisticsListBox, self).change_focus(*args, **kwargs)
        focus = self.get_focus()
        urwid.emit_signal(self, 'focus_changed', focus)


class StatisticsWalker(urwid.TreeWalker):

    signals = ['focus_changed']

    def set_focus(self, focus):
        super(StatisticsWalker, self).set_focus(focus)
        urwid.emit_signal(self, 'focus_changed', focus)


class StatisticsTable(urwid.WidgetWrap):

    column_widths = [
        ('weight', 1),  # function
        (6,),  # total%
        (6,),  # own%
        (6,),  # calls
        (10,),  # total
        (6,),  # total/call
        (10,),  # own
        (6,),  # own/call
    ]
    order = by_total_time

    _active = False

    def __init__(self):
        cls = type(self)
        self._expanded_stat_tokens = set()
        self.walker = StatisticsWalker(NullStatNode())
        on(self.walker, 'focus_changed', self._walker_focus_changed)
        body = StatisticsListBox(self.walker)
        header = urwid.AttrMap(cls.make_columns([
            urwid.Text('FUNCTION'),
            urwid.Text('TOTAL%', 'right'),
            urwid.Text('OWN%', 'right'),
            urwid.Text('CALLS', 'right'),
            urwid.Text('TOTAL', 'right'),
            urwid.Text('/CALL', 'right'),
            urwid.Text('OWN', 'right'),
            urwid.Text('/CALL', 'right'),
        ]), None)
        widget = urwid.Frame(body, header)
        super(StatisticsTable, self).__init__(widget)
        self.update_frame()

    @classmethod
    def make_columns(cls, column_widgets):
        widget_list = []
        for width, widget in zip(cls.column_widths, column_widgets):
            widget_list.append(width + (widget,))
        return urwid.Columns(widget_list, 1)

    @property
    def body(self):
        return self._w.body

    @body.setter
    def body(self, body):
        self._w.body = body

    @property
    def header(self):
        return self._w.header

    @header.setter
    def header(self, header):
        self._w.header = header

    @property
    def footer(self):
        return self._w.footer

    @footer.setter
    def footer(self, footer):
        self._w.footer = footer

    def get_focus(self):
        return self.body.get_focus()

    def set_focus(self, focus):
        self.body.set_focus(focus)

    def set_stats(self, stat):
        node = StatNode(stat, table=self)
        if self.is_browsing():
            self._pending_focus = node
        else:
            self.activate()
            self.set_focus(node)

    def sort_stats(self, order=by_total_time):
        self.order = order

    def activate(self):
        self._active = True
        self.update_frame()

    def inactivate(self):
        self._active = False
        self.update_frame()

    def tokenize_stat(self, stat):
        if stat is None:
            return
        if stat.filename and stat.lineno:
            segments = [stat.filename, str(stat.lineno)]
        elif stat.filename:
            segments = [stat.filename]
        elif stat.lineno:
            segments = ['', str(stat.lineno)]
        else:
            segments = []
        segments.insert(0, stat.name or '')
        return ':'.join(segments)

    def keypress(self, size, key):
        base = super(StatisticsTable, self)
        command = self._command_map[key]
        if command == self._command_map['esc']:
            self.end_browsing()
            return True
        elif command == self._command_map['right']:
            widget, node = self.body.get_focus()
            if widget.expanded:
                heavy_widget = widget.first_child()
                if heavy_widget is not None:
                    heavy_node = heavy_widget.get_node()
                    self.body.change_focus(size, heavy_node)
                return True
        elif command == self._command_map['left']:
            widget, node = self.body.get_focus()
            if not widget.expanded:
                parent_node = node.get_parent()
                if not parent_node.is_root():
                    self.body.change_focus(size, parent_node)
                return True
        elif command == self._command_map[' ']:
            if not self.is_browsing():
                key = 'down'
        return base.keypress(size, key)

    def is_browsing(self, focus=None):
        """Is the user browsing the stats tree?"""
        if focus is None:
            focus = self.get_focus()[1]
        return not focus.is_root()

    def end_browsing(self):
        """Finalizes tree browsing."""
        try:
            node = self._pending_focus
        except AttributeError:
            node = self.get_focus()[1].get_root()
        else:
            del self._pending_focus
        self.set_focus(node)

    def update_frame(self, focus=None):
        if self.is_browsing(focus):
            header_attr = 'header.frozen'
            self.footer = urwid.Text('ESC to melt')
        elif not self._active:
            header_attr = 'header.inactive'
            self.footer = None
        else:
            header_attr = 'header'
            self.footer = None
        self.header.set_attr_map({None: header_attr})

    # signal handlers

    def _walker_focus_changed(self, focus):
        self.update_frame(focus)

    def _widget_expanded(self, widget):
        stat_token = self.tokenize_stat(widget.get_node().get_value())
        self._expanded_stat_tokens.add(stat_token)

    def _widget_collapsed(self, widget):
        stat_token = self.tokenize_stat(widget.get_node().get_value())
        self._expanded_stat_tokens.discard(stat_token)


class StatisticsHeader(urwid.WidgetWrap):

    def __init__(self):
        self.columns = urwid.Columns([])
        margin = EmptyWidget(1)
        widget = urwid.Pile([self.columns, margin])
        super(StatisticsHeader, self).__init__(widget)

    def set_stats(self, stats, src=None, src_time=None):
        fraction_string = '({0}/{1})'.format(
            fmt.format_clock(stats.cpu_time),
            fmt.format_clock(stats.wall_time))
        cpu_info = urwid.Text([
            'CPU ', fmt.markup_percent(stats.cpu_usage),
            ' ', ('weak', fraction_string)])
        if src or src_time:
            if isinstance(src, tuple):
                # ip endpoint
                src_string = '{0}:{1}'.format(*src)
            elif isinstance(src, basestring):
                # file path
                src_string = os.path.basename(src)
            if src_time is not None:
                src_time_string = '{:%H:%M:%S}'.format(src_time)
            if src and src_time:
                markup = [('weak', src_string), ' ', src_time_string]
            elif src:
                markup = src_string
            else:
                markup = src_time_string
            src_info = urwid.Text(markup, align='right')
        else:
            src_info = EmptyWidget()
        opts = ('weight', 1, False)
        self.columns.contents = [(cpu_info, opts), (src_info, opts)]


class StatisticsViewer(object):

    weak_color = 'light green'
    palette = [
        ('weak', weak_color, ''),
        ('focus', 'standout', ''),
        # ui
        ('header', 'dark cyan, standout', ''),
        ('header.frozen', 'dark red, standout', ''),
        ('header.inactive', 'light cyan, standout', ''),
        ('mark', 'dark cyan', ''),
        # risk
        ('danger', 'dark red', ''),
        ('caution', 'light red', ''),
        ('warning', 'brown', ''),
        ('notice', 'dark green', ''),
        # clock
        ('sec', 'brown', ''),
        ('msec', 'dark green', ''),
        ('usec', '', ''),
        # etc
        ('zero', weak_color, ''),
        ('name', 'bold', ''),
        ('module', 'dark blue', ''),
    ]

    focus_map = {x[0]: 'focus' for x in palette}
    focus_map[None] = 'focus'

    def unhandled_input(self, key):
        if key in ('q', 'Q'):
            raise urwid.ExitMainLoop()

    def __init__(self):
        super(StatisticsViewer, self).__init__()
        self.table = StatisticsTable()
        self.header = StatisticsHeader()
        self.widget = urwid.Frame(self.table, self.header)

    def loop(self, *args, **kwargs):
        kwargs.setdefault('unhandled_input', self.unhandled_input)
        loop = urwid.MainLoop(self.widget, self.palette, *args, **kwargs)
        return loop

    def set_stats(self, stats, src=None, src_time=None):
        self.header.set_stats(stats, src, src_time)
        self.table.set_stats(stats)

    def activate(self):
        return self.table.activate()

    def inactivate(self):
        return self.table.inactivate()

    def use_vim_command_map(self):
        urwid.command_map['h'] = urwid.command_map['left']
        urwid.command_map['j'] = urwid.command_map['down']
        urwid.command_map['k'] = urwid.command_map['up']
        urwid.command_map['l'] = urwid.command_map['right']