"""Step 1 -- select the source and target servers and pair up their shares."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...config import Profile
from ...exclusions import DEFAULT_EXCLUSIONS
from ...model import SharePair
from ...shares import ShareInfo, auto_map
from ..widgets import (
    Banner,
    Card,
    Muted,
    PageSubtitle,
    PageTitle,
    SectionTitle,
    primary,
)

_NO_TARGET = "— not mirrored —"


class CredentialsPanel(QWidget):
    """Inline sign-in fields, revealed only when a connection is refused.

    Kept hidden until it is needed so the page stays uncluttered in the normal
    case, where Windows already holds usable credentials for both servers.

    The password lives in this widget and in the Win32 sign-in call, and
    nowhere else: it is never written to a profile, a report, or the log.
    """

    submitted = Signal(str, str)  # username, password

    def __init__(self, parent=None):
        super().__init__(parent)
        self.message = Muted("")

        self.username = QLineEdit()
        self.username.setPlaceholderText("DOMAIN\\user, user@domain, or a local account")

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Password")

        self.sign_in_button = primary(QPushButton("Sign in and connect"))
        self.sign_in_button.clicked.connect(self._submit)
        self.username.returnPressed.connect(self._submit)
        self.password.returnPressed.connect(self._submit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.message)
        layout.addWidget(self.username)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.password, 1)
        row.addWidget(self.sign_in_button)
        layout.addLayout(row)

        self.setVisible(False)

    def _submit(self) -> None:
        if not self.username.text().strip():
            self.message.setText("Enter a user name to sign in with.")
            self.username.setFocus()
            return
        self.submitted.emit(self.username.text().strip(), self.password.text())

    def prompt(self, message: str) -> None:
        """Reveal the fields with an explanation of what went wrong."""
        self.message.setText(message)
        self.setVisible(True)
        self.setEnabled(True)
        (self.password if self.username.text().strip() else self.username).setFocus()

    def set_busy(self, busy: bool) -> None:
        self.setEnabled(not busy)
        self.sign_in_button.setText("Signing in…" if busy else "Sign in and connect")

    def succeeded(self) -> None:
        """Hide the panel again and drop the password from memory."""
        self.password.clear()
        self.set_busy(False)
        self.setVisible(False)


class ConnectPage(QWidget):
    """Server selection, live share enumeration, and share-pair mapping."""

    connect_requested = Signal(str, str)  # role, server
    credentials_submitted = Signal(str, str, str)  # role, username, password
    scan_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_shares: list[ShareInfo] = []
        self._target_shares: list[ShareInfo] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(PageTitle("Source and target"))
        layout.addWidget(
            PageSubtitle(
                "NASSync makes the target an exact mirror of the source. "
                "The source server is only ever read."
            )
        )

        layout.addLayout(self._build_server_row())

        self.banner = Banner()
        self.banner.setVisible(False)
        layout.addWidget(self.banner)

        tabs = QTabWidget()
        tabs.addTab(self._build_mapping_tab(), "Shares")
        tabs.addTab(self._build_exclusions_tab(), "Exclusions")
        tabs.addTab(self._build_performance_tab(), "Performance")
        tabs.addTab(self._build_safety_tab(), "Safety")
        layout.addWidget(tabs, 1)

        footer = QHBoxLayout()
        self.summary_label = Muted("")
        footer.addWidget(self.summary_label, 1)
        self.scan_button = primary(QPushButton("Analyze differences"))
        self.scan_button.setDefault(True)
        self.scan_button.setEnabled(False)
        self.scan_button.clicked.connect(self.scan_requested.emit)
        footer.addWidget(self.scan_button)
        layout.addLayout(footer)

    # --- construction -------------------------------------------------------

    def _build_server_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)

        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("server name")
        self.source_connect = QPushButton("Connect")
        self.source_status = Muted("Not connected")
        self.source_credentials = CredentialsPanel()
        row.addWidget(
            self._server_card(
                "Source",
                "The server being retired — read only",
                self.source_edit,
                self.source_connect,
                self.source_status,
                self.source_credentials,
            ),
            1,
        )

        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("server name")
        self.target_connect = QPushButton("Connect")
        self.target_status = Muted("Not connected")
        self.target_credentials = CredentialsPanel()
        row.addWidget(
            self._server_card(
                "Target",
                "The server being brought up to date",
                self.target_edit,
                self.target_connect,
                self.target_status,
                self.target_credentials,
            ),
            1,
        )

        self.source_credentials.submitted.connect(
            lambda user, secret: self.credentials_submitted.emit("source", user, secret)
        )
        self.target_credentials.submitted.connect(
            lambda user, secret: self.credentials_submitted.emit("target", user, secret)
        )

        self.source_connect.clicked.connect(
            lambda: self.connect_requested.emit("source", self.source_edit.text())
        )
        self.target_connect.clicked.connect(
            lambda: self.connect_requested.emit("target", self.target_edit.text())
        )
        self.source_edit.returnPressed.connect(self.source_connect.click)
        self.target_edit.returnPressed.connect(self.target_connect.click)
        return row

    @staticmethod
    def _server_card(title, description, edit, button, status, credentials) -> Card:
        card = Card(spacing=8)
        card.add(SectionTitle(title))
        card.add(Muted(description))

        entry = QHBoxLayout()
        entry.setSpacing(8)
        entry.addWidget(QLabel("\\\\"))
        entry.addWidget(edit, 1)
        entry.addWidget(button)
        card.add_layout(entry)
        card.add(status)
        card.add(credentials)  # hidden until a connection is refused
        # Keeps both cards top-aligned: without this, revealing the sign-in
        # fields on one card stretches the other card's rows apart to match.
        card.body.addStretch(1)
        return card

    def credentials_panel(self, role: str) -> CredentialsPanel:
        return self.source_credentials if role == "source" else self.target_credentials

    def request_credentials(self, role: str, message: str) -> None:
        """Reveal the sign-in fields for one server after a refused connection."""
        self.set_status(role, "Sign-in required")
        self.credentials_panel(role).prompt(message)

    def set_signing_in(self, role: str, busy: bool) -> None:
        self.credentials_panel(role).set_busy(busy)

    def _build_mapping_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addWidget(
            Muted(
                "Shares with matching names are paired automatically. "
                "Change any target to map it elsewhere, or clear it to leave "
                "that share out entirely."
            )
        )

        self.mapping_table = QTableWidget(0, 3)
        self.mapping_table.setHorizontalHeaderLabels(
            ["Mirror", "Source share", "Target share"]
        )
        self.mapping_table.verticalHeader().setVisible(False)
        self.mapping_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mapping_table.verticalHeader().setDefaultSectionSize(34)
        header = self.mapping_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.mapping_table.itemChanged.connect(self._update_scan_button)
        layout.addWidget(self.mapping_table, 1)

        buttons = QHBoxLayout()
        select_all = QPushButton("Select all")
        select_none = QPushButton("Clear selection")
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        select_none.clicked.connect(lambda: self._set_all_checked(False))
        buttons.addWidget(select_all)
        buttons.addWidget(select_none)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page

    def _build_exclusions_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addWidget(
            Muted(
                "One pattern per line. Excluded items are ignored on both "
                "servers — never copied, and never removed from the target."
            )
        )
        layout.addWidget(
            Muted(
                "A pattern without a backslash matches that file or folder name "
                "at any depth, so @Recycle covers every recycle bin on the "
                "server. A pattern containing a backslash — Archive\\2019 — "
                "matches one specific folder from the share root, along with "
                "everything inside it. Wildcards are allowed in both."
            )
        )
        self.exclusions_edit = QPlainTextEdit("\n".join(DEFAULT_EXCLUSIONS))
        layout.addWidget(self.exclusions_edit, 1)

        row = QHBoxLayout()
        restore = QPushButton("Restore defaults")
        restore.clicked.connect(
            lambda: self.exclusions_edit.setPlainText("\n".join(DEFAULT_EXCLUSIONS))
        )
        row.addWidget(restore)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    def _build_performance_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(12)

        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setSpacing(10)

        self.copy_threads = QSpinBox()
        self.copy_threads.setRange(1, 128)
        self.copy_threads.setValue(16)
        self.copy_threads.setToolTip(
            "Parallel streams within one folder. Copying over SMB spends most "
            "of its time waiting on per-file round trips, so overlapping them "
            "is where nearly all of the throughput comes from."
        )

        self.parallel_directories = QSpinBox()
        self.parallel_directories.setRange(1, 16)
        self.parallel_directories.setValue(3)
        self.parallel_directories.setToolTip(
            "How many folders are copied at once. Helps when the changes are "
            "spread thinly across many folders rather than concentrated in a few."
        )

        self.unbuffered = QCheckBox("Use unbuffered I/O for large files")
        self.unbuffered.setChecked(True)
        self.unbuffered.setToolTip(
            "Avoids filling the system cache with data that is read exactly once."
        )

        self.restartable = QCheckBox("Restartable mode — resume interrupted files")
        self.restartable.setChecked(False)
        self.restartable.setToolTip(
            "Journals every block so an interrupted file resumes instead of "
            "restarting. This is very slow. Enable it only for a link that "
            "genuinely drops mid-transfer."
        )

        form.addRow("Parallel streams per folder", self.copy_threads)
        form.addRow("Folders copied at once", self.parallel_directories)
        form.addRow(self.unbuffered)
        form.addRow(self.restartable)
        outer.addWidget(form_host)

        outer.addWidget(
            Muted(
                "Defaults suit a fast local network. If the target is a NAS "
                "that struggles under load, reduce the two figures above before "
                "changing anything else."
            )
        )
        outer.addStretch(1)
        return page

    def _build_safety_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(12)

        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setSpacing(10)

        self.use_trash = QCheckBox("Move removed items to .nassync-trash on the target")
        self.use_trash.setChecked(True)
        self.use_trash.setToolTip(
            "Removed items are moved into a dated folder on the target share, "
            "keeping their original paths, so a mistaken mirror stays "
            "recoverable until you clear it."
        )

        self.verify_after = QCheckBox("Verify the mirror when the run finishes")
        self.verify_after.setChecked(True)

        self.confirm_before = QCheckBox("Confirm before making any changes")
        self.confirm_before.setChecked(True)

        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.0, 120.0)
        self.tolerance.setSingleStep(0.5)
        self.tolerance.setSuffix(" seconds")
        self.tolerance.setValue(2.0)
        self.tolerance.setToolTip(
            "Timestamps closer together than this count as identical. "
            "Filesystems disagree by a second or two, so zero causes needless "
            "recopying."
        )

        self.retry_count = QSpinBox()
        self.retry_count.setRange(0, 20)
        self.retry_count.setValue(3)
        self.retry_wait = QSpinBox()
        self.retry_wait.setRange(0, 300)
        self.retry_wait.setValue(5)
        self.retry_wait.setSuffix(" seconds")

        form.addRow(self.use_trash)
        form.addRow(self.verify_after)
        form.addRow(self.confirm_before)
        form.addRow("Timestamp tolerance", self.tolerance)
        form.addRow("Attempts for locked files", self.retry_count)
        form.addRow("Wait between attempts", self.retry_wait)
        outer.addWidget(form_host)
        outer.addStretch(1)
        return page

    # --- share lists --------------------------------------------------------

    def set_shares(self, role: str, shares: list[ShareInfo]) -> None:
        """Called when a server's share list arrives."""
        if role == "source":
            self._source_shares = shares
        else:
            self._target_shares = shares
        self.set_status(role, f"Connected — {len(shares)} share(s) available")
        # Whatever got us here worked, so put the sign-in fields away again and
        # drop the password.
        self.credentials_panel(role).succeeded()
        self._rebuild_mapping()

    def set_status(self, role: str, message: str) -> None:
        (self.source_status if role == "source" else self.target_status).setText(message)

    def _rebuild_mapping(self) -> None:
        """Rebuild the pairing table, preserving any manual choices already made."""
        previous = {
            self.mapping_table.item(row, 1).text(): (
                self.mapping_table.cellWidget(row, 2).currentText(),
                self.mapping_table.item(row, 0).checkState() == Qt.Checked,
            )
            for row in range(self.mapping_table.rowCount())
        }

        suggestions = auto_map(self._source_shares, self._target_shares)
        target_names = [s.name for s in self._target_shares]

        self.mapping_table.blockSignals(True)
        self.mapping_table.setRowCount(0)
        for share in self._source_shares:
            row = self.mapping_table.rowCount()
            self.mapping_table.insertRow(row)

            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            self.mapping_table.setItem(row, 0, tick)

            name_item = QTableWidgetItem(share.name)
            name_item.setFlags(Qt.ItemIsEnabled)
            if share.remark:
                name_item.setToolTip(share.remark)
            self.mapping_table.setItem(row, 1, name_item)

            combo = QComboBox()
            combo.addItem(_NO_TARGET)
            combo.addItems(target_names)
            combo.currentIndexChanged.connect(self._update_scan_button)
            self.mapping_table.setCellWidget(row, 2, combo)

            remembered = previous.get(share.name)
            if remembered and remembered[0] in target_names:
                combo.setCurrentText(remembered[0])
                tick.setCheckState(Qt.Checked if remembered[1] else Qt.Unchecked)
            elif suggestions.get(share.name):
                combo.setCurrentText(suggestions[share.name])
                tick.setCheckState(Qt.Checked)
            else:
                tick.setCheckState(Qt.Unchecked)

        self.mapping_table.blockSignals(False)
        self._update_scan_button()

    def _set_all_checked(self, checked: bool) -> None:
        for row in range(self.mapping_table.rowCount()):
            self.mapping_table.item(row, 0).setCheckState(
                Qt.Checked if checked else Qt.Unchecked
            )
        self._update_scan_button()

    def _update_scan_button(self, *_) -> None:
        pairs = self.selected_pairs()
        self.scan_button.setEnabled(bool(pairs))
        if pairs:
            self.summary_label.setText(
                f"{len(pairs)} share pair(s) selected for mirroring"
            )
        elif self.mapping_table.rowCount():
            self.summary_label.setText("Select at least one share to continue")
        else:
            self.summary_label.setText("Connect to both servers to list their shares")

    def selected_pairs(self) -> list[SharePair]:
        """The share pairs the operator has ticked and given a target for."""
        pairs: list[SharePair] = []
        source_server = self.source_edit.text().strip().strip("\\/")
        target_server = self.target_edit.text().strip().strip("\\/")
        for row in range(self.mapping_table.rowCount()):
            if self.mapping_table.item(row, 0).checkState() != Qt.Checked:
                continue
            target_share = self.mapping_table.cellWidget(row, 2).currentText()
            if target_share == _NO_TARGET:
                continue
            pairs.append(
                SharePair(
                    source_server=source_server,
                    source_share=self.mapping_table.item(row, 1).text(),
                    target_server=target_server,
                    target_share=target_share,
                )
            )
        return pairs

    # --- profile round-tripping ---------------------------------------------

    def apply_profile(self, profile: Profile) -> None:
        self.source_edit.setText(profile.source_server)
        self.target_edit.setText(profile.target_server)
        self.exclusions_edit.setPlainText("\n".join(profile.exclusions))

        options = profile.options
        self.use_trash.setChecked(options.use_trash)
        self.verify_after.setChecked(options.verify_after_run)
        self.confirm_before.setChecked(options.confirm_before_execute)
        self.tolerance.setValue(options.mtime_tolerance)
        self.retry_count.setValue(options.retry_count)
        self.retry_wait.setValue(options.retry_wait)
        self.copy_threads.setValue(options.copy_threads)
        self.parallel_directories.setValue(options.parallel_directories)
        self.unbuffered.setChecked(options.unbuffered_large_files)
        self.restartable.setChecked(options.restartable)

        # Show the saved pairs immediately, before either server is contacted.
        self._source_shares = [ShareInfo(p.source_share) for p in profile.pairs]
        self._target_shares = [ShareInfo(p.target_share) for p in profile.pairs]
        self._rebuild_mapping()

        self.mapping_table.blockSignals(True)
        for row in range(self.mapping_table.rowCount()):
            name = self.mapping_table.item(row, 1).text()
            match = next((p for p in profile.pairs if p.source_share == name), None)
            if match is not None:
                self.mapping_table.cellWidget(row, 2).setCurrentText(match.target_share)
                self.mapping_table.item(row, 0).setCheckState(
                    Qt.Checked if match.enabled else Qt.Unchecked
                )
        self.mapping_table.blockSignals(False)
        self._update_scan_button()

    def collect_profile(self, profile: Profile) -> Profile:
        """Write the current form state back into *profile*."""
        profile.source_server = self.source_edit.text().strip().strip("\\/")
        profile.target_server = self.target_edit.text().strip().strip("\\/")
        profile.pairs = self.selected_pairs()
        profile.exclusions = [
            line.strip()
            for line in self.exclusions_edit.toPlainText().splitlines()
            if line.strip()
        ]

        options = profile.options
        options.use_trash = self.use_trash.isChecked()
        options.verify_after_run = self.verify_after.isChecked()
        options.confirm_before_execute = self.confirm_before.isChecked()
        options.mtime_tolerance = self.tolerance.value()
        options.retry_count = self.retry_count.value()
        options.retry_wait = self.retry_wait.value()
        options.copy_threads = self.copy_threads.value()
        options.parallel_directories = self.parallel_directories.value()
        options.unbuffered_large_files = self.unbuffered.isChecked()
        options.restartable = self.restartable.isChecked()
        return profile
