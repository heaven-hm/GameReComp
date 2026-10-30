// projectmodel.h -- everything the GUI shows, read from files the pipeline wrote.
//
// The rule this file exists to enforce: the GUI never decides anything. Every
// status, count and verdict it displays is parsed out of a file that a pipeline
// command produced. If a file is missing the model says so -- it does not infer
// a pass from an absence, which is the one mistake this project has retracted
// more than any other.
#pragma once

#include <QString>
#include <QVector>
#include <QJsonObject>
#include <QStringList>

struct Stage {
    QString name;
    QString status;      // as the pipeline recorded it: ok / failed / skipped
    QString detail;
};

struct FunctionRow {
    QString address;     // "0x00402020"
    QString symbol;
    QString category;    // HIGH_LEVEL_VERIFIED / HIGH_LEVEL_UNVERIFIED / ...
    QString file;        // the module the function came from
    QString sourcePath;  // src/highlevel/<address>.c, empty when not delivered
    QString verifiedBy;  // provenance.verified_by, empty when unverified
    QString notProven;   // provenance.not_proven, the limit stated by the producer
    QStringList evidence; // the instruction lines the name was derived from
};

struct CertifiedRow {
    QString address;
    QString verdict;     // agree / agree-return / agree-frame / diverge / ...
    QString compares;    // exactly what the instrument compared
    int namedMemory = 0;
    int cases = 0;
    QString module;
    QString verdictJson;
};

// One project directory, everything it claims, and where each claim came from.
class ProjectModel {
public:
    // Loads <dir>/reports/report.json, <dir>/manifest.json and the high-level
    // modules the pipeline applied. `error` is non-empty when the directory is
    // not a project; that is shown to the user rather than left as an empty view.
    bool load(const QString &dir, QString *error);

    QString directory() const { return m_dir; }
    bool hasReport() const { return m_hasReport; }
    int score() const { return m_score; }
    QString verification() const { return m_verification; }
    QString pePath() const { return m_pe; }
    QVector<Stage> stages() const { return m_stages; }
    QVector<FunctionRow> functions() const { return m_functions; }
    QVector<CertifiedRow> certified() const { return m_certified; }
    // The certified index lives in the repository, not the project; it is
    // loaded separately because it is evidence for the pipeline as a whole.
    void loadCertifiedIndex(const QString &repoRoot);

    // Counts the GUI shows. They are sums over the rows above, never estimates.
    int countCategory(const QString &category) const;

private:
    QString m_dir, m_pe, m_verification;
    bool m_hasReport = false;
    int m_score = 0;
    QVector<Stage> m_stages, m_unusedStages;
    QVector<FunctionRow> m_functions;
    QVector<CertifiedRow> m_certified;
};
