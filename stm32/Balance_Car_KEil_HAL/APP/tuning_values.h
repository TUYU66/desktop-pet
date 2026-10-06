#ifndef TUNING_VALUES_H
#define TUNING_VALUES_H
#include <stdint.h>

/* Wire values: mid angle x1000; existing PID source gains x100.
 * These bounds are an operator envelope, not automatic tuning recommendations. */
#define TUNING_COUNT 6
static inline int Tuning_ValuesValid(const long *p)
{
    return p[0] >= -10000 && p[0] <= 10000 &&
           p[1] >= 100000 && p[1] <= 2000000 &&
           p[2] >= 0 && p[2] <= 20000 &&
           p[3] >= 0 && p[3] <= 1200000 &&
           p[4] >= 0 && p[4] <= 10000 &&
           p[5] >= 0 && p[5] <= 6000;
}

/* Exact arity and bounded integer parsing; no scanf overflow or suffixes. */
static inline int Tuning_Parse(const char *p, long *out, unsigned int count)
{
    unsigned int i;
    for (i = 0; i < count; ++i) {
        unsigned long n = 0;
        int negative = *p == '-';
        if (negative) ++p;
        if (*p < '0' || *p > '9') return 0;
        do {
            unsigned long digit = (unsigned long)(*p++ - '0');
            if (n > (2147483647UL - digit) / 10UL) return 0;
            n = n * 10UL + digit;
        } while (*p >= '0' && *p <= '9');
        out[i] = negative ? -(long)n : (long)n;
        if (i + 1 == count) return *p == 0;
        if (*p++ != ',') return 0;
    }
    return 0;
}
#endif
