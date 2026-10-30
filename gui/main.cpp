// main.cpp -- RevEng Studio, a Qt 6 Widgets front end over the same backend as
// the CLI. It links no reconstruction logic: it runs `./RevEng` as a subprocess
// and reads the records that command writes, so the GUI and the CLI cannot
// disagree about what happened.
#include "mainwindow.h"

#include <QApplication>
#include <QCommandLineParser>
#include <QDir>
#include <QStandardPaths>
#include <QProcess>
#include <QTextStream>
#include "projectmodel.h"
#include "peinfo.h"

// Headless parity: the same model the window uses, printed and exited. A GUI
// whose data path cannot be exercised without a display is a GUI nobody can
// test, and Phase 5 asks for headless CLI parity rather than a screenshot.
static int selftest(const QString &dir, const QString &repoRoot)
{
    ProjectModel model;
    QString error;
    QTextStream out(stdout);
    if (!model.load(dir, &error)) {
        out << "SELFTEST FAIL: " << error << "\n";
        return 1;
    }
    model.loadCertifiedIndex(repoRoot);
    out << "project:      " << model.directory() << "\n";
    out << "score:        " << model.score() << "\n";
    out << "verification: " << model.verification() << "\n";
    out << "stages:       " << model.stages().size() << "\n";
    for (const auto &s : model.stages())
        out << "  " << s.name.leftJustified(12) << s.status << "\n";
    out << "functions:    " << model.functions().size() << "\n";
    for (const auto &f : model.functions())
        out << "  " << f.address << "  " << f.category << "  " << f.symbol << "\n";
    out << "certified:    " << model.certified().size() << "\n";
    int verified = 0;
    for (const auto &f : model.functions())
        if (f.category == QLatin1String("HIGH_LEVEL_VERIFIED") && !f.verifiedBy.isEmpty())
            ++verified;
    out << "verified-by:  " << verified << "\n";

    // The wizard's toolchain check, in the same headless form: which of the tools
    // a build needs are actually present. A GUI that says "ready" without looking
    // is the same class of claim as a verdict that describes a wider check than
    // the instrument performed.
    QStringList present, missing;
    for (const char *name : {"python3", "cmake", "cc", "pkg-config"}) {
        const QString n = QString::fromLatin1(name);
        (QStandardPaths::findExecutable(n).isEmpty() ? missing : present) << n;
    }
    const bool sdl = !QStandardPaths::findExecutable(QStringLiteral("pkg-config")).isEmpty()
                     && QProcess::execute(QStringLiteral("pkg-config"),
                                          {QStringLiteral("--exists"), QStringLiteral("sdl2")}) == 0;
    {
        const BuildInfo b = model.build();
        out << "build:        ";
        if (!b.present)
            out << "no manifest.json\n";
        else if (b.binaryPath.isEmpty())
            out << "sources only, no binary recorded\n";
        else
            out << (b.digestsMatch ? "digests MATCH (binary built from this source)"
                                   : "DIGESTS DIFFER") << "\n";
    }
    out << "toolchain:    " << (present.isEmpty() ? QStringLiteral("none") : present.join(QStringLiteral(", ")))
        << (missing.isEmpty() ? QString() : QStringLiteral(" | missing: ") + missing.join(QStringLiteral(", ")))
        << " | sdl2: " << (sdl ? "present" : "MISSING") << "\n";
    out.flush();
    return 0;
}

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);
    QApplication::setApplicationName(QStringLiteral("RevEng Studio"));
    QApplication::setApplicationVersion(QStringLiteral("0.1"));

    QCommandLineParser parser;
    parser.setApplicationDescription(
        QStringLiteral("Desktop front end for the RevEng pipeline. Every status it shows is "
                       "read from a record the pipeline wrote."));
    parser.addHelpOption();
    parser.addVersionOption();
    const QCommandLineOption openOpt({QStringLiteral("o"), QStringLiteral("open")},
                                     QStringLiteral("Project directory to open."),
                                     QStringLiteral("dir"));
    parser.addOption(openOpt);
    const QCommandLineOption selftestOpt(QStringLiteral("selftest"),
                                         QStringLiteral("Load a project headlessly, print what the GUI would show, exit."),
                                         QStringLiteral("dir"));
    parser.addOption(selftestOpt);
    const QCommandLineOption repoOpt(QStringLiteral("repo"),
                                     QStringLiteral("Repository root, for reports/certified/."),
                                     QStringLiteral("dir"));
    parser.addOption(repoOpt);
    const QCommandLineOption peOpt(QStringLiteral("selftest-pe"),
                                   QStringLiteral("Inspect an executable headlessly and print the verdict."),
                                   QStringLiteral("file"));
    parser.addOption(peOpt);
    parser.process(app);

    if (parser.isSet(selftestOpt))
        return selftest(parser.value(selftestOpt), parser.value(repoOpt));

    if (parser.isSet(peOpt)) {
        const PeInfo info = inspectPe(parser.value(peOpt));
        QTextStream out(stdout);
        if (!info.ok) {
            out << "PE: not usable: " << info.error << "\n";
            return 1;
        }
        out << "PE: " << info.magic << " " << info.machine
            << " image base 0x" << QString::number(info.imageBase, 16)
            << (info.pe32x86 ? " -- supported target" : " -- NOT a supported target") << "\n";
        out.flush();
        return info.pe32x86 ? 0 : 2;
    }

    MainWindow w;
    if (parser.isSet(openOpt))
        QMetaObject::invokeMethod(&w, "reloadProject", Qt::DirectConnection);
    w.show();
    return app.exec();
}
