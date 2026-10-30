#include "peinfo.h"

#include <QFile>
#include <QDataStream>

namespace {

quint16 u16(const QByteArray &b, int o)
{
    return (o + 2 <= b.size()) ? quint16(quint8(b[o]) | (quint8(b[o + 1]) << 8)) : 0;
}
quint32 u32(const QByteArray &b, int o)
{
    return (o + 4 <= b.size())
               ? (quint32(quint8(b[o])) | (quint32(quint8(b[o + 1])) << 8)
                  | (quint32(quint8(b[o + 2])) << 16) | (quint32(quint8(b[o + 3])) << 24))
               : 0;
}

} // namespace

PeInfo inspectPe(const QString &path)
{
    PeInfo info;
    QFile f(path);
    // Only the headers are needed; a whole 1.4 MB game is not read to find them.
    if (!f.open(QIODevice::ReadOnly) || f.size() < 0x40) {
        info.error = QStringLiteral("cannot read the file");
        return info;
    }
    const QByteArray head = f.read(0x400);
    if (head.size() < 0x40 || quint8(head[0]) != 'M' || quint8(head[1]) != 'Z') {
        info.error = QStringLiteral("no MZ signature -- this is not a DOS/Windows executable");
        return info;
    }
    const quint32 peOff = u32(head, 0x3C);
    if (peOff < 0x40 || peOff + 0x18 > quint32(head.size())) {
        info.error = QStringLiteral("the PE header offset points outside the first 1 KB -- "
                                    "likely not a PE, or a truncated file");
        return info;
    }
    if (quint8(head[int(peOff)]) != 'P' || quint8(head[int(peOff) + 1]) != 'E'
        || head[int(peOff) + 2] != 0 || head[int(peOff) + 3] != 0) {
        info.error = QStringLiteral("no PE\\0\\0 signature at the offset the DOS header names");
        return info;
    }
    const quint16 machine = u16(head, int(peOff) + 4);
    const quint16 optMagic = u16(head, int(peOff) + 24);
    switch (machine) {
    case 0x014c: info.machine = QStringLiteral("i386"); break;
    case 0x8664: info.machine = QStringLiteral("x86-64"); break;
    case 0x01c4: info.machine = QStringLiteral("ARMv7"); break;
    case 0xaa64: info.machine = QStringLiteral("ARM64"); break;
    default:     info.machine = QStringLiteral("unknown (0x%1)").arg(machine, 4, 16, QLatin1Char('0'));
    }
    info.magic = (optMagic == 0x20b) ? QStringLiteral("PE32+") : QStringLiteral("PE32");

    // The pipeline targets 32-bit x86 Windows games. Anything else is reported
    // as out of scope rather than silently attempted: a successful build for
    // an unsupported architecture would be a claim the project cannot back.
    info.pe32x86 = (machine == 0x014c && optMagic == 0x10b);
    const int optOff = int(peOff) + 24;
    info.imageBase = (optMagic == 0x20b) ? u32(head, optOff + 24)
                                         : u32(head, optOff + 28);
    info.ok = true;
    return info;
}
