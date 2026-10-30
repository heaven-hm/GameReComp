// mainwindow.cpp
#include "mainwindow.h"
#include "peinfo.h"

#include <QApplication>
#include <QDir>
#include <QFileDialog>
#include <QCheckBox>
#include <QFileInfo>
#include <QListWidget>
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
#include <QStandardPaths>
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

    // The architecture is validated here rather than left to the pipeline's
    // inspect stage, so the user learns before a run that this build targets
    // 32-bit x86 Windows games and nothing else.
    m_peCheck = new QLabel(paths);
    m_peCheck->setWordWrap(true);
    form->addRow(QStringLiteral("Executable check"), m_peCheck);

    auto *runRow = new QHBoxLayout;
    m_runButton = new QPushButton(QStringLiteral("Run pipeline"), paths);
    m_capture = new QCheckBox(QStringLiteral("capture frames"), paths);
    m_capture->setToolTip(QStringLiteral(
        "Sets RECOMP_SCREENSHOT for the run and shows what the rebuilt game drew. "
        "Frames are evidence of what was rendered, not of what was played."));
    m_summary = new QLabel(QStringLiteral("no project loaded"), paths);
    m_summary->setWordWrap(true);
    runRow->addWidget(m_runButton);
    runRow->addWidget(m_capture);
    runRow->addWidget(m_summary, 1);
    form->addRow(QString(), runRow);

    // The wizard's two remaining obligations: say what tools are present, and say
    // whose assets these are. Neither is decoration -- a run that fails because
    // SDL2 is missing, or a user who thinks assets are bundled, are both failures
    // the GUI can prevent by stating the facts before the run.
    m_tools = new QLabel(paths);
    m_tools->setWordWrap(true);
    m_tools->setTextInteractionFlags(Qt::TextSelectableByMouse);
    form->addRow(QStringLiteral("Toolchain"), m_tools);

    auto *legal = new QLabel(
        QStringLiteral("<b>Your game, your assets.</b> This tool recompiles an executable "
                       "you already own and never downloads, bundles or redistributes game "
                       "data. The delivered project contains source generated from your "
                       "binary; the original files stay where they are."),
        paths);
    legal->setWordWrap(true);
    legal->setStyleSheet(QStringLiteral("color:#5a5a5a;"));
    form->addRow(QStringLiteral("Legal"), legal);

    outer->addWidget(paths);

    // --- the evidence tabs
    m_tabs = new QTabWidget(central);

    m_stages = new QTreeWidget(m_tabs);
    m_stages->setHeaderLabels({QStringLiteral("Stage"), QStringLiteral("Status"), QStringLiteral("Detail")});
    m_stages->header()->setSectionResizeMode(2, QHeaderView::Stretch);
    m_stages->setRootIsDecorated(false);
    m_tabs->addTab(m_stages, QStringLiteral("Stages"));

    // The source explorer: the function list, the generated C, and the
    // provenance beside it. The three panels are the product's claim in one
    // screen -- this function, this source, and exactly what was checked.
    auto *explorer = new QWidget(m_tabs);
    auto *explorerLayout = new QVBoxLayout(explorer);

    m_search = new QLineEdit(explorer);
    m_search->setPlaceholderText(QStringLiteral("filter by address or symbol"));
    explorerLayout->addWidget(m_search);

    auto *split = new QSplitter(Qt::Horizontal, explorer);
    m_functions = new QTableWidget(0, 4, split);
    m_functions->setHorizontalHeaderLabels({QStringLiteral("Address"), QStringLiteral("Symbol"),
                                            QStringLiteral("Category"), QStringLiteral("Verified by")});
    m_functions->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Stretch);
    m_functions->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_functions->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_functions->verticalHeader()->setVisible(false);
    split->addWidget(m_functions);

    auto *rightSplit = new QSplitter(Qt::Vertical, split);
    m_sourceView = new QPlainTextEdit(rightSplit);
    m_sourceView->setReadOnly(true);
    m_sourceView->setPlaceholderText(QStringLiteral("select a function to see the C that was generated for it"));
    m_provenanceView = new QPlainTextEdit(rightSplit);
    m_provenanceView->setReadOnly(true);
    m_provenanceView->setPlaceholderText(QStringLiteral("provenance: what was verified, and what was not"));
    rightSplit->addWidget(m_sourceView);
    rightSplit->addWidget(m_provenanceView);
    rightSplit->setStretchFactor(0, 3);
    rightSplit->setStretchFactor(1, 2);
    split->addWidget(rightSplit);
    split->setStretchFactor(0, 2);
    split->setStretchFactor(1, 3);
    explorerLayout->addWidget(split, 1);

    m_tabs->addTab(explorer, QStringLiteral("Source explorer"));

    m_certified = new QTableWidget(0, 6, m_tabs);
    m_certified->setHorizontalHeaderLabels({QStringLiteral("Address"), QStringLiteral("Verdict"),
                                            QStringLiteral("Named memory"), QStringLiteral("Cases"),
                                            QStringLiteral("What was compared"),
                                            QStringLiteral("Module")});
    m_certified->horizontalHeader()->setSectionResizeMode(4, QHeaderView::Stretch);
    m_certified->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_certified->verticalHeader()->setVisible(false);
    m_tabs->addTab(m_certified, QStringLiteral("Certified"));

    // The reproducibility claim, in the tab a sceptic would open first: which
    // sources were built, which binary came out, and whether the manifest's own
    // two digests agree. No re-derivation here -- a second implementation of a
    // hash is a second answer.
    m_buildView = new QPlainTextEdit(m_tabs);
    m_buildView->setReadOnly(true);
    m_buildView->setPlaceholderText(QStringLiteral("no manifest.json in this project"));
    m_tabs->addTab(m_buildView, QStringLiteral("Build"));

    auto *frames = new QWidget(m_tabs);
    auto *framesLayout = new QHBoxLayout(frames);
    m_frameList = new QListWidget(frames);
    m_frameList->setMaximumWidth(240);
    m_frameView = new QLabel(frames);
    m_frameView->setAlignment(Qt::AlignCenter);
    m_frameView->setMinimumSize(320, 240);
    m_frameView->setTextInteractionFlags(Qt::TextSelectableByMouse);
    framesLayout->addWidget(m_frameList);
    framesLayout->addWidget(m_frameView, 1);
    m_tabs->addTab(frames, QStringLiteral("Frames"));

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
    connect(m_functions, &QTableWidget::cellClicked, this,
            [this](int row, int) { showFunction(row); });
    connect(m_search, &QLineEdit::textChanged, this, [this](const QString &) { refresh(); });
    connect(m_frameList, &QListWidget::currentRowChanged, this, [this](int row) {
        const QStringList f = m_model.frames();
        if (row < 0 || row >= f.size())
            return;
        QPixmap px(f[row]);
        if (px.isNull()) {
            m_frameView->setText(QStringLiteral("cannot read %1").arg(f[row]));
            return;
        }
        m_frameView->setPixmap(px.scaled(m_frameView->size(), Qt::KeepAspectRatio,
                                         Qt::SmoothTransformation));
        m_frameView->setToolTip(QStringLiteral("%1  (%2x%3)")
                                    .arg(QFileInfo(f[row]).fileName())
                                    .arg(px.width()).arg(px.height()));
    });
    connect(m_pePath, &QLineEdit::editingFinished, this, &MainWindow::validatePe);
    refreshToolStatus();
}

void MainWindow::refreshToolStatus()
{
    struct Tool { const char *name; bool required; };
    const Tool tools[] = {
        {"python3", true}, {"cmake", true}, {"cc", true}, {"pkg-config", false},
    };
    QStringList have, missing;
    for (const auto &t : tools) {
        const QString name = QString::fromLatin1(t.name);
        const bool found = !QStandardPaths::findExecutable(name).isEmpty();
        if (found)
            have << name;
        else
            missing << (t.required ? QStringLiteral("**%1**").arg(name) : name);
    }
    // SDL2 is what the runtime actually links; pkg-config is how it is found.
    const bool sdl = !QStandardPaths::findExecutable(QStringLiteral("pkg-config")).isEmpty()
                     && QProcess::execute(QStringLiteral("pkg-config"),
                                          {QStringLiteral("--exists"), QStringLiteral("sdl2")}) == 0;
    QString text = QStringLiteral("present: %1").arg(have.isEmpty() ? QStringLiteral("none") : have.join(QStringLiteral(", ")));
    if (!sdl)
        text += QStringLiteral("   ·   **SDL2 not found via pkg-config** -- the runtime links it");
    if (!missing.isEmpty())
        text += QStringLiteral("   ·   missing: %1").arg(missing.join(QStringLiteral(", ")));
    if (missing.isEmpty() && sdl)
        text += QStringLiteral("   ·   ready to build");
    m_tools->setText(text);
    m_tools->setStyleSheet(missing.isEmpty() && sdl ? QStringLiteral("color:#2e7d32;")
                                                    : QStringLiteral("color:#a15c00;"));
}

void MainWindow::validatePe()
{
    const QString pe = m_pePath->text().trimmed();
    if (pe.isEmpty()) {
        m_peCheck->setText(QStringLiteral("no executable selected"));
        m_peCheck->setStyleSheet(QString());
        return;
    }
    const PeInfo info = inspectPe(pe);
    if (!info.ok) {
        m_peCheck->setText(QStringLiteral("not usable: %1").arg(info.error));
        m_peCheck->setStyleSheet(QStringLiteral("color:#c62828;"));
        return;
    }
    if (info.pe32x86) {
        m_peCheck->setText(QStringLiteral("%1 %2 (i386), image base 0x%3 -- supported target")
                               .arg(info.magic, info.machine)
                               .arg(info.imageBase, 8, 16, QLatin1Char('0')).toUpper());
        m_peCheck->setStyleSheet(QStringLiteral("color:#2e7d32;"));
    } else {
        m_peCheck->setText(QStringLiteral("%1 %2 -- NOT a supported target: this pipeline "
                                          "recompiles 32-bit x86 Windows games. A build here would "
                                          "not mean what the project's claims mean.")
                               .arg(info.magic, info.machine));
        m_peCheck->setStyleSheet(QStringLiteral("color:#a15c00;"));
    }
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
    if (!f.isEmpty()) {
        m_pePath->setText(f);
        validatePe();
    }
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
    {
        const BuildInfo b = m_model.build();
        QStringList lines;
        if (!b.present) {
            lines << QStringLiteral("no manifest.json -- this project has no recorded build.");
        } else {
            lines << QStringLiteral("sources built:  %1 file(s)").arg(b.sourceFiles)
                  << QStringLiteral("source digest:  %1").arg(b.sourceDigest)
                  << QString();
            if (b.binaryPath.isEmpty()) {
                lines << QStringLiteral("binary:         none recorded -- the project has not been built, "
                                        "so nothing here claims it was.");
            } else {
                lines << QStringLiteral("binary:         %1").arg(b.binaryPath)
                      << QStringLiteral("binary sha256:  %1").arg(b.binarySha256)
                      << QStringLiteral("binary size:    %1 bytes").arg(b.binarySize)
                      << QStringLiteral("built from:     %1").arg(b.builtFromDigest)
                      << QString()
                      << (b.digestsMatch
                              ? QStringLiteral("THE CLAIM HOLDS: the binary records the digest of the "
                                               "source tree that is in this project, so the executable "
                                               "was built from exactly these files.")
                              : QStringLiteral("THE CLAIM DOES NOT HOLD HERE: the binary's recorded digest "
                                               "differs from the project's source digest -- either the "
                                               "source changed after the build, or the binary came from "
                                               "somewhere else."))
                      << QString()
                      << QStringLiteral("build command:")
                      << QStringLiteral("  %1").arg(b.buildCommand);
            }
        }
        m_buildView->setPlainText(lines.join(QLatin1Char('\n')));
        m_buildView->moveCursor(QTextCursor::Start);
    }

    m_frameList->clear();
    for (const QString &f : m_model.frames())
        m_frameList->addItem(QFileInfo(f).fileName());
    if (m_model.frames().isEmpty()) {
        m_frameView->setPixmap(QPixmap());
        m_frameView->setText(
            m_model.framesDir().isEmpty()
                ? QStringLiteral("No frames were captured for this project.\n\n"
                                 "Tick \"capture frames\" before a run and the rebuilt game's own "
                                 "drawing is saved here -- that is evidence of what it rendered, "
                                 "not of what was played.")
                : QStringLiteral("A screenshots directory exists but is empty."));
    }

    m_stages->resizeColumnToContents(0);
    m_stages->resizeColumnToContents(1);

    const auto &rows = m_model.functions();
    m_functions->setRowCount(rows.size());
    const QString filter = m_search ? m_search->text().trimmed() : QString();
    for (int i = 0; i < rows.size(); ++i) {
        const auto &f = rows[i];
        m_functions->setItem(i, 0, new QTableWidgetItem(f.address));
        m_functions->setItem(i, 1, new QTableWidgetItem(f.symbol));
        auto *cat = new QTableWidgetItem(f.category);
        if (f.category == QLatin1String("HIGH_LEVEL_VERIFIED"))
            cat->setForeground(QColor(0x2e, 0x7d, 0x32));
        m_functions->setItem(i, 2, cat);
        m_functions->setItem(i, 3, new QTableWidgetItem(f.verifiedBy));
        // Filtering hides rows rather than removing them: the count in the
        // status bar still describes the project, not the current search.
        m_functions->setRowHidden(i, !filter.isEmpty()
                                         && !f.address.contains(filter, Qt::CaseInsensitive)
                                         && !f.symbol.contains(filter, Qt::CaseInsensitive));
    }
    m_functions->resizeColumnToContents(0);
    m_statusBarLabel->setText(QStringLiteral("%1 stage(s), %2 function(s), %3 certified record(s)")
                                  .arg(m_model.stages().size())
                                  .arg(m_model.functions().size())
                                  .arg(m_model.certified().size()));
}

void MainWindow::showFunction(int row)
{
    const auto &rows = m_model.functions();
    if (row < 0 || row >= rows.size())
        return;
    const auto &f = rows[row];

    if (f.sourcePath.isEmpty()) {
        m_sourceView->setPlainText(
            QStringLiteral("no generated source was delivered for %1.\n\n"
                           "The function is recorded in project.json but its .c is not in "
                           "src/highlevel/ -- so this project carries a claim about it with no "
                           "source beside the claim, which is worth knowing rather than hiding.")
                .arg(f.address));
    } else {
        QFile src(QDir(m_model.directory()).filePath(f.sourcePath));
        if (src.open(QIODevice::ReadOnly))
            m_sourceView->setPlainText(QString::fromUtf8(src.readAll()));
        else
            m_sourceView->setPlainText(QStringLiteral("cannot read %1").arg(f.sourcePath));
    }
    m_sourceView->moveCursor(QTextCursor::Start);

    QStringList prov;
    prov << QStringLiteral("address:   %1").arg(f.address)
         << QStringLiteral("symbol:    %1").arg(f.symbol)
         << QStringLiteral("category:  %1").arg(f.category)
         << QStringLiteral("module:    %1").arg(f.file);
    if (!f.verifiedBy.isEmpty())
        prov << QStringLiteral("verified:  %1").arg(f.verifiedBy);
    else
        prov << QStringLiteral("verified:  no -- this function records no verified_by, so it is "
                               "not a certified replacement whatever its category says");
    if (!f.notProven.isEmpty())
        prov << QStringLiteral("not proven: %1").arg(f.notProven);
    if (!f.evidence.isEmpty()) {
        prov << QString() << QStringLiteral("evidence behind the name:");
        for (const auto &e : f.evidence)
            prov << QStringLiteral("  %1").arg(e);
    }
    m_provenanceView->setPlainText(prov.join(QLatin1Char('\n')));
    m_provenanceView->moveCursor(QTextCursor::Start);
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

    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    if (m_capture->isChecked()) {
        const QString shots = QDir(dir).filePath(QStringLiteral("screenshots"));
        QDir().mkpath(shots);
        env.insert(QStringLiteral("RECOMP_SCREENSHOT"), shots);
        m_proc->setProcessEnvironment(env);
    }

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
