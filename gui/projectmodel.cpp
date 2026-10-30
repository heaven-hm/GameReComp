// projectmodel.cpp -- reads the pipeline's own records. Adds nothing to them.
#include "projectmodel.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

namespace {

QJsonObject readJson(const QString &path, bool *ok)
{
    QFile f(path);
    *ok = false;
    if (!f.open(QIODevice::ReadOnly))
        return {};
    QJsonParseError err{};
    const auto doc = QJsonDocument::fromJson(f.readAll(), &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject())
        return {};
    *ok = true;
    return doc.object();
}

} // namespace

bool ProjectModel::load(const QString &dir, QString *error)
{
    m_dir = dir;
    m_stages.clear();
    m_functions.clear();
    m_hasReport = false;
    m_score = 0;
    m_verification.clear();
    m_pe.clear();

    if (!QFileInfo(dir).isDir()) {
        *error = QStringLiteral("%1 is not a directory").arg(dir);
        return false;
    }

    bool ok = false;
    const QJsonObject report = readJson(QDir(dir).filePath(QStringLiteral("reports/report.json")), &ok);
    if (!ok) {
        // Not an error the user caused -- an unbuilt directory simply has no
        // record yet. Say that rather than showing an empty dashboard, which
        // would look like a project that ran and produced nothing.
        *error = QStringLiteral("no reports/report.json -- run the pipeline over this directory first");
        return false;
    }
    m_hasReport = true;
    m_score = report.value(QStringLiteral("score")).toInt();

    const QJsonObject outcome = report.value(QStringLiteral("outcome")).toObject();
    m_verification = outcome.value(QStringLiteral("status")).toString();

    const QJsonArray stages = report.value(QStringLiteral("stages")).toArray();
    for (const auto &s : stages) {
        const QJsonObject o = s.toObject();
        Stage st;
        st.name = o.value(QStringLiteral("name")).toString();
        st.status = o.value(QStringLiteral("status")).toString();
        const QJsonObject data = o.value(QStringLiteral("data")).toObject();
        st.detail = data.value(QStringLiteral("detail")).toString();
        if (st.detail.isEmpty() && data.contains(QStringLiteral("score")))
            st.detail = QStringLiteral("score %1").arg(data.value(QStringLiteral("score")).toInt());
        m_stages.append(st);
    }

    // The high-level modules the pipeline actually applied, with their
    // provenance. A category without verified_by is shown as it is recorded;
    // the GUI does not upgrade or downgrade it.
    m_frames.clear();
    m_framesDir.clear();
    // Frames live where a screenshot-enabled run put them. Sorted by name, which
    // is the order they were written.
    QDir shots(QDir(dir).filePath(QStringLiteral("screenshots")));
    if (shots.exists()) {
        m_framesDir = shots.absolutePath();
        for (const QString &f : shots.entryList({QStringLiteral("*.png"), QStringLiteral("*.bmp")},
                                                QDir::Files, QDir::Name))
            m_frames.append(shots.absoluteFilePath(f));
    }

    m_build = BuildInfo{};
    const QJsonObject manifest = readJson(QDir(dir).filePath(QStringLiteral("manifest.json")), &ok);
    if (ok) {
        m_pe = manifest.value(QStringLiteral("pe")).toString();
        m_build.present = true;
        m_build.sourceDigest = manifest.value(QStringLiteral("source_digest_sha256")).toString();
        m_build.sourceFiles = manifest.value(QStringLiteral("source_files")).toArray().size();
        const QJsonObject bin = manifest.value(QStringLiteral("binary")).toObject();
        if (!bin.isEmpty()) {
            m_build.binaryPath = bin.value(QStringLiteral("path")).toString();
            m_build.binarySha256 = bin.value(QStringLiteral("sha256")).toString();
            m_build.binarySize = qint64(bin.value(QStringLiteral("size")).toDouble());
            m_build.buildCommand = bin.value(QStringLiteral("build_command")).toString();
            m_build.builtFromDigest = bin.value(QStringLiteral("built_from_source_digest")).toString();
            // The claim, and only the claim: these two strings are equal or they
            // are not, and a reader deserves the boolean rather than two digests to
            // compare by eye.
            m_build.digestsMatch = !m_build.sourceDigest.isEmpty()
                                   && m_build.sourceDigest == m_build.builtFromDigest;
        }
    }

    // The provenance of the readable source lives in project.json, written by
    // the project stage: per function, its address, symbol, category and the
    // verified_by that a category of HIGH_LEVEL_VERIFIED requires. Reading
    // src/highlevel/ instead finds the .c files and no provenance at all, which
    // is how a GUI ends up displaying readable source with no idea which of it
    // was checked.
    const QJsonObject project = readJson(QDir(dir).filePath(QStringLiteral("project.json")), &ok);
    if (ok) {
        const QJsonObject pe = project.value(QStringLiteral("pe")).toObject();
        if (m_pe.isEmpty())
            m_pe = pe.value(QStringLiteral("path")).toString();

        for (const auto &fv : project.value(QStringLiteral("high_level_functions")).toArray()) {
            const QJsonObject f = fv.toObject();
            FunctionRow row;
            row.address = f.value(QStringLiteral("address")).toString();
            row.symbol = f.value(QStringLiteral("symbol")).toString();
            row.category = f.value(QStringLiteral("category")).toString();
            row.file = f.value(QStringLiteral("module")).toString();
            const QJsonObject prov = f.value(QStringLiteral("provenance")).toObject();
            row.verifiedBy = prov.value(QStringLiteral("verified_by")).toString();
            row.notProven = prov.value(QStringLiteral("not_proven")).toString();
            for (const auto &e : prov.value(QStringLiteral("evidence")).toArray())
                row.evidence << e.toString();
            // The delivered source is named after the address. If it is not
            // there the explorer says so instead of showing an empty pane that
            // looks like a function with no body.
            const QString rel = QStringLiteral("src/highlevel/%1.c").arg(row.address);
            if (QFileInfo(QDir(dir).filePath(rel)).isFile())
                row.sourcePath = rel;
            m_functions.append(row);
        }
    }
    return true;
}

void ProjectModel::loadCertifiedIndex(const QString &repoRoot)
{
    m_certified.clear();
    bool ok = false;
    const QJsonObject idx = readJson(
        QDir(repoRoot).filePath(QStringLiteral("reports/certified/index.json")), &ok);
    if (!ok)
        return;
    for (const auto &rv : idx.value(QStringLiteral("rows")).toArray()) {
        const QJsonObject r = rv.toObject();
        CertifiedRow c;
        c.address = r.value(QStringLiteral("address")).toString();
        c.verdict = r.value(QStringLiteral("verdict")).toString();
        c.compares = r.value(QStringLiteral("compares")).toString();
        c.namedMemory = r.value(QStringLiteral("named_memory")).toArray().size();
        c.cases = r.value(QStringLiteral("cases")).toInt();
        c.module = r.value(QStringLiteral("module")).toString();
        c.verdictJson = r.value(QStringLiteral("verdict_json")).toString();
        m_certified.append(c);
    }
}

int ProjectModel::countCategory(const QString &category) const
{
    int n = 0;
    for (const auto &f : m_functions)
        if (f.category == category)
            ++n;
    return n;
}
