#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <math.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef CGError (*SLSSetDisplayRotationFunction)(CGDirectDisplayID, int32_t);

static SLSSetDisplayRotationFunction rotation_setter(void) {
    static void *framework = NULL;
    static SLSSetDisplayRotationFunction function = NULL;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        framework = dlopen("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_LAZY);
        if (framework) function = (SLSSetDisplayRotationFunction)dlsym(framework, "SLSSetDisplayRotation");
    });
    return function;
}

static BOOL parse_uint(const char *text, uint32_t *value) {
    char *end = NULL;
    unsigned long parsed = strtoul(text, &end, 10);
    if (end == text || !end || *end || parsed > UINT32_MAX) return NO;
    *value = (uint32_t)parsed;
    return YES;
}

static BOOL valid_rotation(uint32_t degrees) {
    switch (degrees) {
        case 0: case 90: case 180: case 270: return YES;
        default: return NO;
    }
}

static CGError set_rotation(CGDirectDisplayID display, uint32_t degrees) {
    SLSSetDisplayRotationFunction setter = rotation_setter();
    if (!setter) return kCGErrorFailure;
    return setter(display, (int32_t)degrees);
}

static BOOL wait_for_rotation(CGDirectDisplayID display, uint32_t degrees, double seconds) {
    for (double elapsed = 0; elapsed < seconds; elapsed += 0.1) {
        if ((uint32_t)lround(CGDisplayRotation(display)) % 360 == degrees) return YES;
        usleep(100000);
    }
    return (uint32_t)lround(CGDisplayRotation(display)) % 360 == degrees;
}

static BOOL restore_rotation(CGDirectDisplayID display, uint32_t original) {
    CGError result = set_rotation(display, original);
    return result == kIOReturnSuccess && wait_for_rotation(display, original, 5.0);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 2 && strcmp(argv[1], "--check-private-api") == 0) {
            if (rotation_setter()) {
                puts("AVAILABLE: SkyLight SLSSetDisplayRotation");
                return 0;
            }
            puts("UNAVAILABLE: SkyLight SLSSetDisplayRotation");
            return 10;
        }
        if (argc != 3) {
            fprintf(stderr, "Expected displayID and rotation angle (0, 90, 180, or 270).\n");
            return 2;
        }
        uint32_t displayID = 0, target = 0;
        if (!parse_uint(argv[1], &displayID) || !parse_uint(argv[2], &target) ||
            !valid_rotation(target)) {
            puts("ERROR: Rotation must be 0, 90, 180, or 270 degrees.");
            return 2;
        }
        CGDirectDisplayID online[32];
        CGDisplayCount onlineCount = 0;
        CGError listError = CGGetOnlineDisplayList(32, online, &onlineCount);
        BOOL foundOnline = NO;
        if (listError == kCGErrorSuccess)
            for (CGDisplayCount index = 0; index < onlineCount; index++)
                foundOnline |= online[index] == displayID;
        if (!foundOnline || !CGDisplayIsOnline(displayID) || !CGDisplayIsActive(displayID)) {
            puts("ERROR: The selected display is not online and active.");
            return 3;
        }
        if (CGDisplayIsInMirrorSet(displayID)) {
            puts("ERROR: Rotation switching is disabled for mirrored displays.");
            return 3;
        }

        uint32_t original = (uint32_t)lround(CGDisplayRotation(displayID)) % 360;
        if (original == target) {
            puts("ERROR: That orientation is already active.");
            return 4;
        }
        if (!rotation_setter()) {
            puts("ERROR: This macOS version does not expose the display rotation service.");
            return 4;
        }

        CGError result = set_rotation(displayID, target);
        if (result != kIOReturnSuccess) {
            fprintf(stdout, "ERROR: macOS rejected the orientation request (CoreGraphics error %d).\n", (int)result);
            return 5;
        }
        if (!wait_for_rotation(displayID, target, 5.0)) {
            BOOL restored = restore_rotation(displayID, original);
            puts(restored ?
                 "ERROR: macOS did not apply the requested orientation; the previous orientation was restored." :
                 "ERROR: Orientation was not confirmed and automatic restore failed. Use System Settings > Displays to restore it.");
            return 5;
        }
        puts("PREVIEW_APPLIED");
        fflush(stdout);

        struct pollfd input = { .fd = STDIN_FILENO, .events = POLLIN | POLLHUP };
        int ready = poll(&input, 1, 25000);
        char response[32] = {0};
        ssize_t length = ready > 0 ? read(STDIN_FILENO, response, sizeof(response) - 1) : 0;
        if (length > 0 && strncmp(response, "keep", 4) == 0) {
            puts("KEPT_SESSION_ONLY");
            fflush(stdout);
            return 0;
        }
        if (restore_rotation(displayID, original)) {
            puts("REVERTED");
            fflush(stdout);
            return 0;
        }
        puts("ERROR: Automatic restore failed. Use System Settings > Displays to restore the previous orientation.");
        return 8;
    }
}
