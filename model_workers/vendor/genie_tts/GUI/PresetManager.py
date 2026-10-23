import os
import json
from typing import Callable, Optional, Dict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QGroupBox, QMessageBox, QInputDialog
)
from PySide6.QtCore import Signal, QSettings, Slot


class PresetManager(QWidget):
    # 信号：通知主界面加载数据
    sig_load_state = Signal(dict)

    def __init__(self,
                 presets_file: str,
                 state_getter: Callable[[], dict],
                 parent: QWidget = None):
        super().__init__(parent)
        self.presets_file: str = presets_file
        self.state_getter: Callable[[], dict] = state_getter
        self.presets: Dict[str, dict] = {}
        self.current_preset_name: Optional[str] = None
        self._init_ui()
        self._load_from_disk()
        # 初始化完成后，应用一次初始状态
        self._apply_initial_preset()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("预设管理 (自动保存)")
        h_layout = QHBoxLayout()

        self.lbl_info = QLabel("当前:")

        self.combo_presets = QComboBox()
        self.combo_presets.textActivated.connect(self._on_preset_switch_triggered)

        btn_new = QPushButton("新建")
        btn_new.clicked.connect(self.create_preset)

        # === 新增：重命名按钮 ===
        btn_rename = QPushButton("重命名")
        btn_rename.clicked.connect(self.rename_preset)
        # =====================

        btn_del = QPushButton("删除")
        btn_del.clicked.connect(self.delete_preset)

        h_layout.addWidget(self.lbl_info)
        h_layout.addWidget(self.combo_presets, 1)
        h_layout.addWidget(btn_new)
        h_layout.addWidget(btn_rename)  # 添加到布局
        h_layout.addWidget(btn_del)

        group.setLayout(h_layout)
        layout.addWidget(group)

    @property
    def current_preset_data(self) -> dict:
        return self.presets.get(self.current_preset_name, {})

    def _load_from_disk(self):
        """从磁盘加载 JSON"""
        if os.path.exists(self.presets_file):
            try:
                with open(self.presets_file, 'r', encoding='utf-8') as f:
                    self.presets = json.load(f)
            except Exception as e:
                print(f"[ERROR] 预设文件损坏: {e}")
                self.presets = {}

        if not self.presets:
            self.presets = {"Default": {}}

        self.combo_presets.clear()
        self.combo_presets.addItems(list(self.presets.keys()))

    def _save_to_disk(self):
        """写入磁盘"""
        try:
            os.makedirs(os.path.dirname(self.presets_file), exist_ok=True)
            with open(self.presets_file, 'w', encoding='utf-8') as f:
                json.dump(self.presets, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] 保存预设失败: {e}")

    def _apply_initial_preset(self):
        """初始化时恢复上次的选择"""
        last_used = QSettings("MyTTS", "GUI").value("last_preset", "Default")
        if last_used not in self.presets and self.combo_presets.count() > 0:
            last_used = self.combo_presets.itemText(0)
        self.current_preset_name = last_used
        self.combo_presets.setCurrentText(last_used)
        self._load_preset_data(last_used)

    @Slot(str)
    def _on_preset_switch_triggered(self, new_preset_name: str):
        if new_preset_name == self.current_preset_name:
            return
        if self.current_preset_name:
            self._save_current_state_to_memory(self.current_preset_name)
        self._load_preset_data(new_preset_name)
        self.current_preset_name = new_preset_name
        QSettings("MyTTS", "GUI").setValue("last_preset", new_preset_name)
        self._save_to_disk()

    def _save_current_state_to_memory(self, preset_name: str):
        """调用回调获取主界面状态，并更新到内存字典"""
        if self.state_getter and preset_name in self.presets:
            current_data = self.state_getter()
            self.presets[preset_name] = current_data

    def _load_preset_data(self, preset_name: str):
        """发送信号给主界面加载数据"""
        data = self.presets.get(preset_name, {})
        self.sig_load_state.emit(data)
        print(f"[INFO] 已加载预设: {preset_name}")

    # ================= 公共接口 =================

    def create_preset(self):
        """新建预设"""
        if self.current_preset_name:
            self._save_current_state_to_memory(self.current_preset_name)

        name, ok = QInputDialog.getText(self, "新建预设", "名称:")
        if ok and name:
            if name in self.presets:
                QMessageBox.warning(self, "警告", "预设名已存在")
                return
            self.presets[name] = {}
            self.combo_presets.addItem(name)
            self.combo_presets.setCurrentText(name)
            self.current_preset_name = name
            self._save_to_disk()
            self._load_preset_data(name)
            print(f"[INFO] 已创建预设: {name}")

    def rename_preset(self):
        """重命名当前预设"""
        current_name = self.current_preset_name
        if not current_name:
            return

        # 先保存当前状态到内存，确保重命名时带走的是最新数据
        self._save_current_state_to_memory(current_name)

        new_name, ok = QInputDialog.getText(self, "重命名预设", "新名称:", text=current_name)
        if ok and new_name and new_name != current_name:
            if new_name in self.presets:
                QMessageBox.warning(self, "警告", "预设名已存在")
                return
            # 迁移数据
            self.presets[new_name] = self.presets.pop(current_name)
            self.current_preset_name = new_name
            # 更新下拉框显示的文本（更新当前选中的这一项）
            current_index = self.combo_presets.currentIndex()
            self.combo_presets.setItemText(current_index, new_name)
            # 更新配置记录
            QSettings("MyTTS", "GUI").setValue("last_preset", new_name)
            self._save_to_disk()
            print(f"[INFO] 已重命名预设: {current_name} -> {new_name}")

    def delete_preset(self):
        """删除当前预设"""
        target = self.current_preset_name
        if len(self.presets) <= 1:
            QMessageBox.warning(self, "禁止", "至少保留一个预设")
            return

        if QMessageBox.StandardButton.Yes == QMessageBox.question(self, "确认", f"删除 '{target}'?"):
            del self.presets[target]
            self.combo_presets.removeItem(self.combo_presets.currentIndex())
            new_name = self.combo_presets.currentText()
            self.current_preset_name = new_name
            self._load_preset_data(new_name)
            self._save_to_disk()
            print(f"[INFO] 已删除预设: {target}")

    def shutdown(self):
        """关闭时触发"""
        if self.current_preset_name:
            self._save_current_state_to_memory(self.current_preset_name)
        self._save_to_disk()
