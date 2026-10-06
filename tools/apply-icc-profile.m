#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ColorSync/ColorSync.h>

static int fail(NSString *message, int status) {
    fprintf(stderr, "%s\n", message.UTF8String ?: "ColorSync operation failed");
    return status;
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        BOOL checkOnly = argc == 4 && strcmp(argv[1], "--check") == 0;
        if ((!checkOnly && argc != 3) || (checkOnly && argc != 4))
            return fail(@"Usage: apply-icc-profile [--check] <display-id> <profile-url-or-path>", 2);
        int argumentOffset = checkOnly ? 2 : 1;
        char *end = NULL;
        unsigned long rawDisplayID = strtoul(argv[argumentOffset], &end, 10);
        if (!end || *end != '\0' || rawDisplayID == 0 || rawDisplayID > UINT32_MAX)
            return fail(@"Invalid display ID.", 2);
        NSString *input = [NSString stringWithUTF8String:argv[argumentOffset + 1]];
        NSURL *candidate = [NSURL URLWithString:input];
        NSURL *url = candidate.isFileURL ? candidate.URLByStandardizingPath :
                     [NSURL fileURLWithPath:input].URLByStandardizingPath;
        if (!url.isFileURL || ![NSFileManager.defaultManager isReadableFileAtPath:url.path])
            return fail(@"The selected ICC profile is not readable.", 4);

        ColorSyncProfileRef profile = ColorSyncProfileCreateWithURL((__bridge CFURLRef)url, NULL);
        if (!profile) return fail(@"ColorSync could not open the selected ICC profile.", 4);
        CFDataRef header = ColorSyncProfileCopyHeader(profile);
        BOOL isMonitorProfile = NO;
        if (header && CFDataGetLength(header) >= 16) {
            const UInt8 *bytes = CFDataGetBytePtr(header) + 12;
            isMonitorProfile = bytes[3] == 'm' && bytes[2] == 'n' &&
                               bytes[1] == 't' && bytes[0] == 'r';
        }
        BOOL verified = ColorSyncProfileVerify(profile, NULL, NULL);
        if (header) CFRelease(header);
        CFRelease(profile);
        if (!isMonitorProfile || !verified)
            return fail(@"Only valid display (monitor-class) ICC profiles can be applied.", 4);

        // A mirrored display can be present and color-managed without being
        // reported as an independently active CoreGraphics display. Let
        // ColorSync's device registration check below decide whether this
        // display ID can receive a profile.
        CFUUIDRef uuid = CGDisplayCreateUUIDFromDisplayID((uint32_t)rawDisplayID);
        if (!uuid) return fail(@"Could not identify the selected display to ColorSync.", 5);
        CFDictionaryRef deviceInfo = ColorSyncDeviceCopyDeviceInfo(kColorSyncDisplayDeviceClass, uuid);
        if (!deviceInfo) {
            CFRelease(uuid);
            return fail(@"ColorSync has not registered this display as a color device.", 5);
        }

        if (checkOnly) {
            CFStringRef description = CFDictionaryGetValue(
                deviceInfo, kColorSyncDeviceDescription);
            NSString *deviceName = description ? (__bridge NSString *)description : @"Selected display";
            printf("READY: %s; profile readable and valid; display registered with ColorSync.\n",
                   deviceName.UTF8String ?: "Selected display");
            CFRelease(deviceInfo);
            CFRelease(uuid);
            return 0;
        }

        // The special default-profile key replaces the display's default profile without
        // changing its advertised/factory profile registration.
        NSDictionary *profileInfo = @{
            (__bridge NSString *)kColorSyncDeviceDefaultProfileID: url
        };
        BOOL applied = ColorSyncDeviceSetCustomProfiles(
            kColorSyncDisplayDeviceClass, uuid, (__bridge CFDictionaryRef)profileInfo);
        CFRelease(deviceInfo);
        CFRelease(uuid);
        if (!applied)
            return fail(@"ColorSync rejected this profile for the selected display.", 6);

        puts("APPLIED");
        return 0;
    }
}
