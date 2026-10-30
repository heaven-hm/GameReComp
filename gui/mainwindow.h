// mainwindow.h -- the dashboard, the function list and the evidence view.
//
// Three views over one model, because the product's claim is a chain: a stage
// ran, a function was generated, a verdict was recorded. Showing any one of
// them alone is what makes a dashboard look better than the project is.
#pragma once

#include <QMainWindow>
#include <QProcess>
#include "projectmodel.h"

class QLabel;
class QLineEdit;
class QPlainTextEdit;
class QTableWidget;
class QTreeWidget;
class QTabWidget;
class QPushButton;

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(QWidget *parent = nullptr);

private slots:
    void browseProject();
    void browsePe();
    void runPipeline();
    void pipelineFinished(int exitCode, QProcess::ExitStatus status);
    void pipelineOutput();
    void reloadProject();

private:
    void buildUi();
    void refresh();

    ProjectModel m_model;
    QProcess *m_proc = nullptr;

    QLineEdit *m_projectDir = nullptr;
    QLineEdit *m_pePath = nullptr;
    QLineEdit *m_assets = nullptr;
    QLabel *m_summary = nullptr;
    QLabel *m_statusBarLabel = nullptr;
    QTreeWidget *m_stages = nullptr;
    QTableWidget *m_functions = nullptr;
    QTableWidget *m_certified = nullptr;
    QPlainTextEdit *m_log = nullptr;
    QTabWidget *m_tabs = nullptr;
    QPushButton *m_runButton = nullptr;
};
