"""マップ上のクリック地点にあるレイヤを選択するQGISプラグイン。"""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import QAction, QMenu, QToolBar
from qgis.core import QgsProject
from qgis.gui import QgsMapTool, QgsMapToolIdentify


# QGIS 3（Qt 5）とQGIS 4（Qt 6）では、Qtの一部の定数の書き方が異なります。
# まずQGIS 3の書き方を試し、利用できない場合はQGIS 4の書き方を使います。
try:
    TOOL_BUTTON_ICON_ONLY = Qt.ToolButtonIconOnly
    TOP_TOOLBAR_AREA = Qt.TopToolBarArea
    DELETE_ON_CLOSE = Qt.WA_DeleteOnClose
    CROSS_CURSOR = Qt.CrossCursor
    LEFT_BUTTON = Qt.LeftButton
    DECORATION_ROLE = Qt.DecorationRole
except AttributeError:
    TOOL_BUTTON_ICON_ONLY = Qt.ToolButtonStyle.ToolButtonIconOnly
    TOP_TOOLBAR_AREA = Qt.ToolBarArea.TopToolBarArea
    DELETE_ON_CLOSE = Qt.WidgetAttribute.WA_DeleteOnClose
    CROSS_CURSOR = Qt.CursorShape.CrossCursor
    LEFT_BUTTON = Qt.MouseButton.LeftButton
    DECORATION_ROLE = Qt.ItemDataRole.DecorationRole


class KKSelectLayerForMap:
    """QGIS本体とマップ上のクリック処理をつなぐプラグイン本体。"""

    def __init__(self, iface):
        # ifaceは、QGIS本体がプラグインに渡す操作用のオブジェクトです。
        # マップキャンバスやレイヤパネルなど、QGISの画面を操作するために使います。
        self.iface = iface
        self.canvas = iface.mapCanvas()

        # initGui()で作成する画面部品を保存しておきます。
        # 保存しておくと、unload()でプラグインを無効化するときに削除できます。
        self.action = None
        self.toolbar = None
        self.map_tool = None
        self.previous_map_tool = None

    def initGui(self):
        """プラグイン有効化時にツールバーとマップツールを準備する。"""

        # ツールバーに表示するボタンを作ります。
        # icon.svgは、このプラグインフォルダー内にあるアイコン画像です。
        self.action = QAction(
            QIcon(os.path.join(os.path.dirname(__file__), "icon.svg")),
            "マップ上でレイヤを選択",
            self.iface.mainWindow(),
        )

        # ボタンを押すたびに、マップツールを有効・無効に切り替えます。
        self.action.setCheckable(True)
        self.action.triggered.connect(self._toggle_map_tool)

        # このプラグイン専用のツールバーを作成します。
        self.toolbar = QToolBar("KK_select_layer_for_mapツールバー", self.iface.mainWindow())
        self.toolbar.setObjectName("KK_select_layer_for_map_toolbar")
        # ツールバーにはアイコンだけを表示し、ボタンの文字は表示しません。
        self.toolbar.setToolButtonStyle(TOOL_BUTTON_ICON_ONLY)
        self.toolbar.addAction(self.action)
        self.iface.mainWindow().addToolBar(TOP_TOOLBAR_AREA, self.toolbar)

        # マップキャンバスをクリックしたときに処理する専用ツールを作ります。
        self.map_tool = _LayerIdentifyMapTool(self.canvas, self)

    def unload(self):
        """プラグイン無効化時に、作成した画面部品を片付ける。"""

        # プラグインを無効化した時点で、このツールが有効なら元に戻します。
        if self.map_tool is not None and self.canvas.mapTool() is self.map_tool:
            self._restore_previous_map_tool()

        # QGIS画面からボタンと専用ツールバーを削除します。
        if self.action is not None:
            self.action.deleteLater()
        if self.toolbar is not None:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()

        # 使わなくなったオブジェクトへの参照を消します。
        self.map_tool = None
        self.action = None
        self.toolbar = None

    def _toggle_map_tool(self, checked):
        """ツールバーのボタンに応じて、マップツールを有効・無効にする。"""

        if checked:
            # 後で元に戻せるように、現在使っているマップツールを覚えます。
            self.previous_map_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self.map_tool)
        elif self.canvas.mapTool() is self.map_tool:
            self._restore_previous_map_tool()

    def _restore_previous_map_tool(self):
        """クリック処理を終了し、元のマップツールに戻す。"""

        previous_tool = self.previous_map_tool
        self.previous_map_tool = None
        if previous_tool is not None:
            self.canvas.setMapTool(previous_tool)
        else:
            # 元のマップツールがなかった場合は、現在のツールを解除します。
            self.canvas.unsetMapTool(self.map_tool)
        if self.action is not None:
            self.action.setChecked(False)

    def select_layer(self, layer):
        """指定されたレイヤをレイヤパネルで選択し、必要ならグループを展開する。"""

        layer_tree_view = self.iface.layerTreeView()
        layer_node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if layer_node is not None:
            # レイヤの親をたどり、レイヤが見えるようにすべてのグループを展開します。
            parent = layer_node.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()

        # レイヤパネル上で対象レイヤを選択し、画面内にスクロールします。
        layer_tree_view.setCurrentLayer(layer)
        layer_tree_view.scrollTo(layer_tree_view.currentIndex())

    def layers_at_point(self, canvas_x, canvas_y):
        """指定された画面座標にあるレイヤを、重複なしで返す。"""

        # プロジェクトに登録されている全レイヤを対象にします。
        # ここではレイヤの表示・非表示を条件にしないため、非表示レイヤも調べられます。
        layers = [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if layer.isValid()
        ]
        if not layers:
            return []

        # QGISの標準識別機能を使い、クリック地点にある地物を調べます。
        identify = QgsMapToolIdentify(self.canvas)
        results = identify.identify(
            canvas_x,
            canvas_y,
            layers,
            QgsMapToolIdentify.TopDownAll,
        )

        # 1つのレイヤに複数の地物があっても、レイヤは1回だけリストに追加します。
        found_layers = []
        found_ids = set()
        for result in results:
            layer = result.mLayer
            if layer is not None and layer.id() not in found_ids:
                found_layers.append(layer)
                found_ids.add(layer.id())
        return found_layers

    def show_layer_menu(self, layers, global_pos):
        """複数の候補レイヤを選ぶポップアップメニューを表示する。"""

        menu = QMenu(self.iface.mainWindow())
        menu.setAttribute(DELETE_ON_CLOSE)
        for layer in layers:
            # 非表示レイヤも候補に含まれるため、名前に状態を付けて区別します。
            layer_node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
            layer_name = layer.name()
            if layer_node is not None and not layer_node.isVisible():
                layer_name += "（非表示）"
            action = menu.addAction(layer_name)
            if layer_node is not None:
                # レイヤパネルと同じ凡例アイコンを、メニュー項目にも表示します。
                icon = self._layer_style_icon(layer_node)
                if not icon.isNull():
                    action.setIcon(icon)
            action.triggered.connect(
                lambda checked=False, selected_layer=layer: self.select_layer(
                    selected_layer
                )
            )
        menu.popup(global_pos)

    def _layer_style_icon(self, layer_node):
        """レイヤパネルの凡例から、最初のスタイルアイコンを取得する。"""

        model = self.iface.layerTreeView().layerTreeModel()
        legend_nodes = model.layerLegendNodes(layer_node)
        for legend_node in legend_nodes:
            icon = legend_node.data(DECORATION_ROLE)
            if isinstance(icon, QIcon):
                return icon
            if isinstance(icon, QPixmap) and not icon.isNull():
                return QIcon(icon)
        return QIcon()


class _LayerIdentifyMapTool(QgsMapTool):
    """マップキャンバス上のクリックを受け取るQGIS用マップツール。"""

    def __init__(self, canvas, plugin):
        super().__init__(canvas)
        self.plugin = plugin

        # このツールが有効な間、マウスポインターを十字にします。
        self.setCursor(CROSS_CURSOR)

    def canvasReleaseEvent(self, event):
        """マウスボタンを離したときに、クリック地点のレイヤを処理する。"""

        # 右クリックなどでは処理せず、左クリックだけを対象にします。
        if event.button() != LEFT_BUTTON:
            return

        # 画面上のクリック位置から、該当するレイヤを調べます。
        layers = self.plugin.layers_at_point(event.pos().x(), event.pos().y())
        if len(layers) == 1:
            # 候補が1つなら、メニューを出さずにすぐ選択します。
            self.plugin.select_layer(layers[0])
        elif len(layers) > 1:
            # 候補が複数なら、クリック位置の近くに選択メニューを表示します。
            self.plugin.show_layer_menu(layers, self.canvas().mapToGlobal(event.pos()))
