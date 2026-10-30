// peinfo.h -- just enough PE header to tell a user what they selected.
//
// The wizard validates the architecture before a run rather than letting the
// pipeline fail at inspect with a message the user has to go and read. This is
// not a second PE parser: it reads the DOS signature, the PE offset and the
// machine word, which is all "is this a PE, and what kind" needs.
#pragma once

#include <QString>

struct PeInfo {
    bool ok = false;
    QString error;       // why it is not usable, when ok is false
    QString machine;     // "i386", "x86-64", "ARM64", ...
    QString magic;       // "PE32" or "PE32+"
    bool pe32x86 = false; // the architecture this pipeline targets today
    unsigned long long imageBase = 0;
};

PeInfo inspectPe(const QString &path);
