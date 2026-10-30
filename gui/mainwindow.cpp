// mainwindow.cpp
#include "mainwindow.h"

#include <QApplication>
#include <QDir>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QLabel>
#include <QLineEdit>
#include <QPlainTextEdit>
#include <QProcess>
#include <QPushButton>
#include <QSplitter>
#include <QStatusBar>
#include <QTabWidget>
#include <QTableWidget>
#include <QTreeWidget>
#include <QVBoxLayout>

namespace {

QString repoRoot()
{
    // The GUI lives in <repo>/gui; evidence such as reports/certified/ belongs
    // to the repository rather than to any one project directory.
    QDir d(QCoreApplication::applicationDirPath());
    while (!d.isRoot()) {
        if (d.exists(QStringLiteral("tools/autorecomp/report.py")) ||
            d.exists(QStringLiteral("reports/certified/index.json")))
            return d.absolutePath();
        d.cdUp();
    }
    return QDir::currentPath();
}

QColor statusColour(const QString &status)
{
    if (status == QLatin1String("ok"))
        return QColor(0x2e, 0x7d, 0x32);
    if (status == QLatin1String("failed"))
        return QColor(0xc6, 0x28, 0x28);
    return QColor(0x75, 0x75, 0x75);
}

} // namespace

MainWindow::MainWindow(QWidget *parent) : QMainWindow(parent)
{
    buildUi();
    setWindowTitle(QStringLiteral("RevEng Studio"));
    resize(1180, 760);
    refresh();
}

void MainWindow::buildUi()
{
    auto *central = new QWidget(this);
    auto *outer = new QVBoxLayout(central);

    // --- the inputs. A project directory is the only required one; the PE and
    // assets are needed to run the pipeline but not to inspect a finished one.
    auto *paths = new QGroupBox(QStringLiteral("Project"), central);
    auto *form = new QFormLayout(paths);

    auto *projRow = new QHBoxLayout;
    m_projectDir = new QLineEdit(paths);
    auto *projBrowse = new QPushButton(QStringLiteral("Browse…"), paths);
    auto *projReload = new QPushButton(QStringLiteral("Reload"), paths);
    projRow->addWidget(m_projectDir);
    projRow->addWidget(projBrowse);
    projRow->addWidget(projReload);
    form->addRow(QStringLiteral("Project directory"), projRow);

    auto *peRow = new QHBoxLayout;
    m_pePath = new QLineEdit(paths);
    auto *peBrowse = new QPushButton(QStringLiteral("Browse…"), paths);
    peRow->addWidget(m_pePath);
    peRow->addWidget(peBrowse);
    form->addRow(QStringLiteral("Original executable"), peRow);

    m_assets = new QLineEdit(paths);
    form->addRow(QStringLiteral("Asset directory"), m_assets);

    auto *runRow = new QHBoxLayout;
    m_runButton = new QPushButton(QStringLiteral("Run pipeline"), paths);
    m_summary = new QLabel(QStringLiteral("no project loaded"), paths);
    m_summary->setWordWrap(true);
    runRow->addWidget(m_runButton);
    runRow->addWidget(m_summary, 1);
    form->addRow(QString(), runRow);

    outer->addWidget(paths);

    // --- the evidence tabs
    m_tabs = new QTabWidget(central);

    m_stages = new QTreeWidget(m_tabs);
    m_stages->setHeaderLabels({QStringLiteral("Stage"), QStringLiteral("Status"), QStringLiteral("Detail")});
    m_stages->header()->setSectionResizeMode(2, QHeaderView::Stretch);
    m_stages->setRootIsDecorated(false);
    m_tabs->addTab(m_stages, QStringLiteral("Stages"));

    m_functions = new QTableWidget(0, 5, m_tabs);
    m_functions->setHorizontalHeaderLabels({QStringLiteral("Address"), QStringLiteral("Symbol"),
                                            QStringLiteral("Category"), QStringLiteral("Verified by"),
                                            QStringLiteral("File")});
    m_functions->horizontalHeader()->setSectionResizeMode(3, QHeaderView::Stretch);
    m_functions->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_functions->verticalHeader()->setVisible(false);
    m_tabs->addTab(m_functions, QStringLiteral("Source functions"));

    m_certified = new QTableWidget(0, 6, m_tabs);
    m_certified->setHorizontalHeaderLabels({QStringLiteral("Address"), QStringLiteral("Verdict"),
                                            QStringLiteral("Named memory"), QStringLiteral("Cases"),
                                            QStringLiteral("What was compared"),
                                            QStringLiteral("Module")});
    m_certified->horizontalHeader()->setSectionResizeMode(4, QHeaderView::Stretch);
    m_certified->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_certified->verticalHeader()->setVisible(false);
    m_tabs->addTab(m_certified, QStringLiteral("Certified"));

    m_log = new QPlainTextEdit(m_tabs);
    m_log->setReadOnly(true);
    m_log->setPlaceholderText(QStringLiteral("pipeline output appears here as it runs"));
    m_tabs->addTab(m_log, QStringLiteral("Log"));

    outer->addWidget(m_tabs, 1);
    setCentralWidget(central);

    m_statusBarLabel = new QLabel(QStringLiteral("ready"), this);
    statusBar()->addWidget(m_statusBarLabel);

    connect(projBrowse, &QPushButton::clicked, this, &MainWindow::browseProject);
    connect(peBrowse, &QPushButton::clicked, this, &MainWindow::browsePe);
    connect(projReload, &QPushButton::clicked, this, &MainWindow::reloadProject);
    connect(m_runButton, &QPushButton::clicked, this, &MainWindow::runPipeline);
    connect(m_projectDir, &QLineEdit::editingFinished, this, &MainWindow::reloadProject);
}

void MainWindow::browseProject()
{
    const QString dir = QFileDialog::getExistingDirectory(this, QStringLiteral("Project directory"),
                                                          m_projectDir->text());
    if (dir.isEmpty())
        return;
    m_projectDir->setText(dir);
    reloadProject();
}

void MainWindow::browsePe()
{
    const QString f = QFileDialog::getOpenFileName(this, QStringLiteral("Original executable"),
                                                   QFileInfo(m_pePath->text()).absolutePath(),
                                                   QStringLiteral("Windows executables (*.exe);;All files (*)"));
    if (!f.isEmpty())
        m_pePath->setText(f);
}

void MainWindow::reloadProject()
{
    refresh();
}

void MainWindow::refresh()
{
    const QString dir = m_projectDir->text().trimmed();
    if (dir.isEmpty()) {
        m_summary->setText(QStringLiteral("no project loaded"));
        return;
    }

    QString error;
    if (!m_model.load(dir, &error)) {
        m_summary->setText(error);
        m_stages->clear();
        m_functions->setRowCount(0);
        return;
    }
    m_model.loadCertifiedIndex(repoRoot());

    m_summary->setText(QStringLiteral("score %1/100 · verification %2 · %3 source function(s) · %4 certified address(es) in the repository")
                           .arg(m_model.score())
                           .arg(m_model.verification().isEmpty() ? QStringLiteral("—") : m_model.verification())
                           .arg(m_model.functions().size())
                           .arg(m_model.certified().size()));

    m_stages->clear();
    for (const auto &s : m_model.stages()) {
        auto *item = new QTreeWidgetItem(m_stages);
        item->setText(0, s.name);
        item->setText(1, s.status.isEmpty() ? QStringLiteral("—") : s.status);
        item->setText(2, s.detail);
        item->setForeground(1, statusColour(s.status));
    }
    m_stages->resizeColumnToContents(0);
    m_stages->resizeColumnToContents(1);

    m_functions->setRowCount(m_model.functions().size());
    for (int i = 0; i < m_model.functions().size(); ++i) {
        const auto &f = m_model.functions()[i];
        m_functions->setItem(i, 0, new QTableWidgetItem(f.address));
        m_functions->setItem(i, 1, new QTableWidgetItem(f.symbol));
        auto *cat = new QTableWidgetItem(f.category);
        if (f.category == QLatin1String("HIGH_LEVEL_VERIFIED"))
            cat->setForeground(QColor(0x2e, 0x7d, 0x32));
        m_functions->setItem(i, 2, cat);
        m_functions->setItem(i, 3, new QTableWidgetItem(f.verifiedBy));
        m_functions->setItem(i, 4, new QTableWidgetItem(f.file));
    }
    m_functions->resizeColumnToContents(0);
    m_statusBarLabel->setText(QStringLiteral("%1 stage(s), %2 function(s), %3 certified record(s)")
                                  .arg(m_model.stages().size())
                                  .arg(m_model.functions().size())
                                  .arg(m_model.certified().size()));
}

void MainWindow::runPipeline()
{
    if (m_proc)
        return;
    const QString pe = m_pePath->text().trimmed();
    const QString dir = m_projectDir->text().trimmed();
    if (pe.isEmpty() || dir.isEmpty()) {
        m_summary->setText(QStringLiteral("both an executable and a project directory are needed to run"));
        return;
    }

    m_proc = new QProcess(this);
    m_proc->setWorkingDirectory(repoRoot());
    m_proc->setProcessChannelMode(QProcess::MergedChannels);
    connect(m_proc, &QProcess::readyReadStandardOutput, this, &MainWindow::pipelineOutput);
    connect(m_proc, &QProcess::finished, this, &MainWindow::pipelineFinished);

    QStringList args{QStringLiteral("RevEng"), pe, QStringLiteral("--project-dir"), dir,
                     QStringLiteral("--timeout"), QStringLiteral("12"), QStringLiteral("--no-table")};
    if (!m_assets->text().trimmed().isEmpty())
        args << QStringLiteral("--assets-dir") << m_assets->text().trimmed();

    m_log->clear();
    m_log->appendPlainText(QStringLiteral("$ ./%1").arg(args.join(QLatin1Char(' '))));
    m_runButton->setEnabled(false);
    m_statusBarLabel->setText(QStringLiteral("running…"));
    m_proc->start(args.takeFirst(), args);
}

void MainWindow::pipelineOutput()
{
    if (!m_proc)
        return;
    m_log->appendPlainText(QString::fromUtf8(m_proc->readAllStandardOutput()).trimmed());
}

void MainWindow::pipelineFinished(int exitCode, QProcess::ExitStatus status)
{
    if (!m_proc)
        return;
    pipelineOutput();
    m_log->appendPlainText(QStringLiteral("\n[exit %1%2]")
                               .arg(exitCode)
                               .arg(status == QProcess::CrashExit ? QStringLiteral(", crashed") : QString()));
    m_proc->deleteLater();
    m_proc = nullptr;
    m_runButton->setEnabled(true);
    m_statusBarLabel->setText(QStringLiteral("finished; reloading the record"));
    refresh();
}
