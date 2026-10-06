#import <Foundation/Foundation.h>
#import <ColorSync/ColorSync.h>

static NSString *signatureAt(CFDataRef header, CFIndex offset) {
    if (!header || CFDataGetLength(header) < offset + 4) return @"";
    const UInt8 *bytes = CFDataGetBytePtr(header) + offset;
    // ColorSync returns the ICC header's four-character signatures in host byte order.
    char signature[4] = { (char)bytes[3], (char)bytes[2], (char)bytes[1], (char)bytes[0] };
    return [[NSString alloc] initWithBytes:signature length:4 encoding:NSASCIIStringEncoding] ?: @"";
}

static uint16_t readBigEndian16(const UInt8 *bytes) {
    return ((uint16_t)bytes[0] << 8) | bytes[1];
}

static NSString *creationDateAtURL(NSURL *url) {
    NSData *data = [NSData dataWithContentsOfURL:url options:NSDataReadingMappedIfSafe error:nil];
    if (data.length < 36) return @"";
    const UInt8 *bytes = data.bytes;
    uint16_t year = readBigEndian16(bytes + 24);
    uint16_t month = readBigEndian16(bytes + 26);
    uint16_t day = readBigEndian16(bytes + 28);
    uint16_t hour = readBigEndian16(bytes + 30);
    uint16_t minute = readBigEndian16(bytes + 32);
    uint16_t second = readBigEndian16(bytes + 34);
    if (year == 0 || month < 1 || month > 12 || day < 1 ||
        hour > 23 || minute > 59 || second > 59) return @"";
    static const uint8_t daysByMonth[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    uint8_t maxDay = daysByMonth[month - 1];
    if (month == 2 && (year % 400 == 0 || (year % 4 == 0 && year % 100 != 0))) maxDay = 29;
    if (day > maxDay) return @"";
    return [NSString stringWithFormat:@"%04u-%02u-%02u %02u:%02u:%02u",
            year, month, day, hour, minute, second];
}

static void addProfile(NSURL *url, NSMutableArray *profiles, NSMutableSet *seen) {
    NSString *key = url.URLByStandardizingPath.path.stringByResolvingSymlinksInPath;
    if (!key.length || [seen containsObject:key]) return;

    ColorSyncProfileRef profile = ColorSyncProfileCreateWithURL((__bridge CFURLRef)url, NULL);
    if (!profile) return;
    CFDataRef header = ColorSyncProfileCopyHeader(profile);
    NSString *profileClass = signatureAt(header, 12);
    if (![profileClass isEqualToString:@"mntr"] ||
        !ColorSyncProfileVerify(profile, NULL, NULL)) {
        if (header) CFRelease(header);
        CFRelease(profile);
        return;
    }

    CFStringRef description = ColorSyncProfileCopyDescriptionString(profile);
    NSMutableDictionary *record = [NSMutableDictionary dictionary];
    record[@"name"] = description ? CFBridgingRelease(description) : url.lastPathComponent;
    record[@"created"] = creationDateAtURL(url);
    record[@"url"] = url.absoluteString ?: @"";
    [profiles addObject:record];
    [seen addObject:key];
    if (header) CFRelease(header);
    CFRelease(profile);
}

int main(void) {
    @autoreleasepool {
        NSMutableArray *profiles = NSMutableArray.array;
        NSMutableSet *seen = NSMutableSet.set;
        NSArray<NSString *> *roots = @[
            [NSHomeDirectory() stringByAppendingPathComponent:@"Library/ColorSync/Profiles"],
            @"/Library/ColorSync/Profiles",
            @"/System/Library/ColorSync/Profiles"
        ];
        NSFileManager *manager = NSFileManager.defaultManager;
        for (NSString *root in roots) {
            NSDirectoryEnumerator *enumerator = [manager enumeratorAtURL:[NSURL fileURLWithPath:root]
                includingPropertiesForKeys:@[NSURLIsRegularFileKey]
                options:NSDirectoryEnumerationSkipsHiddenFiles errorHandler:nil];
            for (NSURL *url in enumerator) {
                NSString *extension = url.pathExtension.lowercaseString;
                if ([extension isEqualToString:@"icc"] || [extension isEqualToString:@"icm"])
                    addProfile(url, profiles, seen);
            }
        }

        [profiles sortUsingComparator:^NSComparisonResult(NSDictionary *a, NSDictionary *b) {
            return [a[@"name"] localizedCaseInsensitiveCompare:b[@"name"]];
        }];
        NSData *json = [NSJSONSerialization dataWithJSONObject:profiles options:0 error:nil];
        if (!json) return 2;
        fwrite(json.bytes, 1, json.length, stdout);
        fputc('\n', stdout);
        return 0;
    }
}
