"""Dialogs for creating students and registrations."""
from __future__ import annotations

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QLineEdit, QSpinBox, QVBoxLayout)

from core.schema import AREA_EVENT_FIT, COURSE_TO_DEPARTMENT, EXPERIENCE_ORDER, ROLE_EVENT_FIT
from gui.widgets import Notice


class StudentDialog(QDialog):
    """Create a pseudonymous student. No names, e-mails, phone or roll numbers."""

    def __init__(self, parent=None, suggested_id: str = ""):
        super().__init__(parent)
        self.setWindowTitle("New student")
        self.setMinimumWidth(460)
        f = QFormLayout()
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.sid = QLineEdit(suggested_id)
        self.sid.setPlaceholderText("Pseudonymous code, e.g. STU300")
        self.course = QComboBox()
        self.course.setEditable(True)
        self.course.addItems(sorted(COURSE_TO_DEPARTMENT))
        self.year = QSpinBox()
        self.year.setRange(1, 5)
        self.vol = QCheckBox("Currently a UBA volunteer")
        self.part = QCheckBox("Took part in a UBA event before")
        self.exp = QComboBox()
        self.exp.addItems(EXPERIENCE_ORDER)
        self.area = QComboBox()
        self.area.addItems(list(AREA_EVENT_FIT))
        self.role = QComboBox()
        self.role.addItems(list(ROLE_EVENT_FIT))
        self.att = QSpinBox()
        self.att.setRange(0, 500)
        self.reg = QSpinBox()
        self.reg.setRange(0, 500)
        self.reg.setSpecialValueText("unknown")
        self.eng = QSpinBox()
        self.eng.setRange(-1, 100)
        self.eng.setSpecialValueText("unknown")
        self.eng.setValue(-1)
        self.dist = QDoubleSpinBox()
        self.dist.setRange(-0.1, 200)
        self.dist.setDecimals(1)
        self.dist.setSpecialValueText("unknown")
        self.dist.setValue(-0.1)
        for lab, w in (("Student ID", self.sid), ("Course", self.course), ("Year", self.year), ("", self.vol),
                       ("", self.part), ("Experience", self.exp), ("Area of interest", self.area),
                       ("Team applied for", self.role), ("Events attended before", self.att),
                       ("Events registered before", self.reg), ("Engagement score", self.eng),
                       ("Distance to venue (km)", self.dist)):
            f.addRow(lab, w)
        self.err = Notice()
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(self.err)
        lay.addWidget(bb)

    def values(self) -> dict:
        v = dict(student_id=self.sid.text().strip(), course=self.course.currentText().strip(), year=self.year.value(),
                 currently_uba_volunteer=int(self.vol.isChecked()),
                 participated_uba_event_before=int(self.part.isChecked()), experience_level=self.exp.currentText(),
                 area_of_interest=self.area.currentText(), role=self.role.currentText(),
                 previous_event_attendance=self.att.value())
        if self.reg.value() > 0:
            v["previous_events_registered"] = self.reg.value()
        if self.eng.value() >= 0:
            v["academic_engagement_score"] = self.eng.value()
        if self.dist.value() >= 0:
            v["distance_from_venue_km"] = self.dist.value()
        return v


class RegistrationDialog(QDialog):
    def __init__(self, parent, students: list[str], event_date: str | None):
        super().__init__(parent)
        self.setWindowTitle("Add registration")
        self.setMinimumWidth(420)
        f = QFormLayout()
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.student = QComboBox()
        self.student.setEditable(True)
        self.student.addItems(students)
        self.use_date = QCheckBox("Registration date known")
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.days = QSpinBox()
        self.days.setRange(0, 365)
        self.days.setValue(7)
        self.exam = QCheckBox("Student has an exam within about a week of the event")
        self.use_date.toggled.connect(lambda on: (self.date.setEnabled(on), self.days.setEnabled(not on)))
        self.use_date.setChecked(False)
        self.date.setEnabled(False)
        if not event_date:
            self.use_date.setEnabled(False)
            self.use_date.setToolTip("The event has no date; enter the days before instead.")
        f.addRow("Student", self.student)
        f.addRow("", self.use_date)
        f.addRow("Registered on", self.date)
        f.addRow("Days before event", self.days)
        f.addRow("", self.exam)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(bb)

    def values(self) -> dict:
        v = dict(student_id=self.student.currentText().strip(), has_exam_near_event=int(self.exam.isChecked()))
        if self.use_date.isChecked():
            v["registered_on"] = self.date.date().toString("yyyy-MM-dd")
        else:
            v["registration_days_before"] = self.days.value()
        return v
