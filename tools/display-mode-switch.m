#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#include <errno.h>
#include <math.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void print_error(const char *message, CGError error) {
    fprintf(stdout, "ERROR: %s (CoreGraphics error %d)\n", message, (int)error);
    fflush(stdout);
}

static BOOL number_arg(const char *text, double *value) {
    char *end = NULL;
    errno = 0;
    double parsed = strtod(text, &end);
    if (errno || end == text || *end != '\0') return NO;
    *value = parsed;
    return YES;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        // displayID modeID width height pixelWidth pixelHeight refreshRate ioFlags
        if (argc != 9) {
            fprintf(stderr, "Expected displayID, modeID, dimensions, refresh rate and I/O flags.\n");
            return 2;
        }
        char *end = NULL;
        CGDirectDisplayID display = (CGDirectDisplayID)strtoul(argv[1], &end, 10);
        if (!end || *end) return 2;
        uint32_t modeID = (uint32_t)strtoul(argv[2], &end, 10);
        if (!end || *end) return 2;
        double width, height, pixelWidth, pixelHeight, refresh, ioFlags;
        if (!number_arg(argv[3], &width) || !number_arg(argv[4], &height) ||
            !number_arg(argv[5], &pixelWidth) || !number_arg(argv[6], &pixelHeight) ||
            !number_arg(argv[7], &refresh) || !number_arg(argv[8], &ioFlags)) return 2;

        if (!CGDisplayIsOnline(display) || !CGDisplayIsActive(display)) {
            fprintf(stdout, "ERROR: The selected display is not online and active.\n");
            return 3;
        }

        CGDirectDisplayID mirror_parent = CGDisplayMirrorsDisplay(display);
        BOOL is_mirror_slave = mirror_parent != kCGNullDirectDisplay;
        BOOL has_mirror_children = NO;
        uint32_t active_count = 0;
        CGError list_error = CGGetActiveDisplayList(0, NULL, &active_count);
        CGDirectDisplayID *active_displays = NULL;
        CGDirectDisplayID *original_mirror_targets = NULL;
        if (list_error == kCGErrorSuccess && active_count > 0) {
            active_displays = calloc(active_count, sizeof(CGDirectDisplayID));
            original_mirror_targets = calloc(active_count, sizeof(CGDirectDisplayID));
            if (!active_displays || !original_mirror_targets ||
                CGGetActiveDisplayList(active_count, active_displays, &active_count) != kCGErrorSuccess) {
                free(active_displays);
                free(original_mirror_targets);
                fprintf(stdout, "ERROR: Could not inspect the active mirror group.\n");
                return 3;
            }
            for (uint32_t index = 0; index < active_count; index++) {
                original_mirror_targets[index] = CGDisplayMirrorsDisplay(active_displays[index]);
                has_mirror_children |= active_displays[index] != display &&
                    original_mirror_targets[index] == display;
            }
        }
        BOOL is_in_mirror_set = CGDisplayIsInMirrorSet(display);
        if (list_error != kCGErrorSuccess || (is_in_mirror_set &&
            !is_mirror_slave && !has_mirror_children)) {
            free(active_displays);
            free(original_mirror_targets);
            fprintf(stdout, "ERROR: Could not determine a safe display role in the mirror group.\n");
            return 3;
        }

        NSDictionary *options = @{ (__bridge NSString *)kCGDisplayShowDuplicateLowResolutionModes: @YES };
        CFArrayRef modes = CGDisplayCopyAllDisplayModes(display, (__bridge CFDictionaryRef)options);
        CGDisplayModeRef target = NULL;
        if (modes) {
            for (CFIndex i = 0; i < CFArrayGetCount(modes); i++) {
                CGDisplayModeRef candidate = (CGDisplayModeRef)CFArrayGetValueAtIndex(modes, i);
                if (CGDisplayModeGetIODisplayModeID(candidate) != modeID ||
                    CGDisplayModeGetWidth(candidate) != (size_t)width ||
                    CGDisplayModeGetHeight(candidate) != (size_t)height ||
                    CGDisplayModeGetPixelWidth(candidate) != (size_t)pixelWidth ||
                    CGDisplayModeGetPixelHeight(candidate) != (size_t)pixelHeight ||
                    CGDisplayModeGetIOFlags(candidate) != (uint32_t)ioFlags ||
                    fabs(CGDisplayModeGetRefreshRate(candidate) - refresh) > 0.01) continue;
                target = CGDisplayModeRetain(candidate);
                break;
            }
            CFRelease(modes);
        }
        if (!target) {
            free(active_displays);
            free(original_mirror_targets);
            fprintf(stdout, "ERROR: That exact desktop mode is no longer available. Refresh the display list and try again.\n");
            return 4;
        }

        CGDisplayModeRef original = CGDisplayCopyDisplayMode(display);
        if (!original) {
            free(active_displays);
            free(original_mirror_targets);
            CGDisplayModeRelease(target);
            fprintf(stdout, "ERROR: Unable to read the current display mode.\n");
            return 5;
        }
        if (is_mirror_slave &&
            (CGDisplayModeGetWidth(target) != CGDisplayModeGetWidth(original) ||
             CGDisplayModeGetHeight(target) != CGDisplayModeGetHeight(original) ||
             CGDisplayModeGetPixelWidth(target) != CGDisplayModeGetPixelWidth(original) ||
             CGDisplayModeGetPixelHeight(target) != CGDisplayModeGetPixelHeight(original))) {
            free(active_displays);
            free(original_mirror_targets);
            CGDisplayModeRelease(original);
            CGDisplayModeRelease(target);
            fprintf(stdout, "ERROR: A display mirrored to another screen can only change refresh rate.\n");
            return 4;
        }
        if (CGDisplayModeGetIODisplayModeID(original) == modeID &&
            fabs(CGDisplayModeGetRefreshRate(original) - refresh) < 0.01) {
            free(active_displays);
            free(original_mirror_targets);
            CGDisplayModeRelease(original);
            CGDisplayModeRelease(target);
            fprintf(stdout, "ERROR: That mode is already active.\n");
            return 6;
        }

        CGError error = CGDisplaySetDisplayMode(display, target, NULL);
        if (error != kCGErrorSuccess) {
            print_error("Could not preview the selected mode", error);
            free(active_displays);
            free(original_mirror_targets);
            CGDisplayModeRelease(original);
            CGDisplayModeRelease(target);
            return 7;
        }
        CGDisplayModeRef preview = CGDisplayCopyDisplayMode(display);
        BOOL previewMatches = preview &&
            CGDisplayModeGetIODisplayModeID(preview) == modeID &&
            CGDisplayModeGetWidth(preview) == (size_t)width &&
            CGDisplayModeGetHeight(preview) == (size_t)height &&
            CGDisplayModeGetPixelWidth(preview) == (size_t)pixelWidth &&
            CGDisplayModeGetPixelHeight(preview) == (size_t)pixelHeight &&
            fabs(CGDisplayModeGetRefreshRate(preview) - refresh) <= 0.01;
        if (preview) CGDisplayModeRelease(preview);
        if (!previewMatches) {
            CGError restoreError = CGDisplaySetDisplayMode(display, original, NULL);
            print_error("The system did not report the requested mode after applying it", kCGErrorFailure);
            if (restoreError != kCGErrorSuccess)
                print_error("Automatic restore failed; use System Settings > Displays to restore a mode", restoreError);
            free(active_displays);
            free(original_mirror_targets);
            CGDisplayModeRelease(original);
            CGDisplayModeRelease(target);
            return 7;
        }
        BOOL mirror_group_preserved = YES;
        if (is_in_mirror_set) {
            mirror_group_preserved = CGDisplayMirrorsDisplay(display) == mirror_parent;
            for (uint32_t index = 0; mirror_group_preserved && index < active_count; index++)
                mirror_group_preserved &=
                    CGDisplayMirrorsDisplay(active_displays[index]) == original_mirror_targets[index];
        }
        if (!mirror_group_preserved) {
            CGError restoreError = CGDisplaySetDisplayMode(display, original, NULL);
            fprintf(stdout, "ERROR: The mode preview changed the mirror relationship; the requested mode was reverted.\n");
            if (restoreError != kCGErrorSuccess)
                print_error("Automatic restore failed; check the display arrangement in System Settings", restoreError);
            free(active_displays);
            free(original_mirror_targets);
            CGDisplayModeRelease(original);
            CGDisplayModeRelease(target);
            return 7;
        }
        puts("PREVIEW_APPLIED");
        fflush(stdout);

        struct pollfd input = { .fd = STDIN_FILENO, .events = POLLIN | POLLHUP };
        int ready = poll(&input, 1, 25000);
        char response[32] = {0};
        ssize_t length = ready > 0 ? read(STDIN_FILENO, response, sizeof(response) - 1) : 0;
        BOOL keep = length > 0 && strncmp(response, "keep", 4) == 0;
        BOOL saved = NO;
        if (keep) {
            CGDisplayConfigRef config = NULL;
            error = CGBeginDisplayConfiguration(&config);
            if (error == kCGErrorSuccess)
                error = CGConfigureDisplayWithDisplayMode(config, display, target, NULL);
            if (error == kCGErrorSuccess)
                error = CGCompleteDisplayConfiguration(config, kCGConfigurePermanently);
            else if (config)
                CGCancelDisplayConfiguration(config);
            if (error == kCGErrorSuccess) {
                saved = YES;
                puts("SAVED");
                fflush(stdout);
            } else {
                print_error("Could not save the selected display mode", error);
            }
        }
        if (!saved) {
            error = CGDisplaySetDisplayMode(display, original, NULL);
            if (error == kCGErrorSuccess) {
                puts("REVERTED");
                fflush(stdout);
            } else {
                print_error("Automatic restore failed; use System Settings > Displays to restore a mode", error);
            }
        }
        free(active_displays);
        free(original_mirror_targets);
        CGDisplayModeRelease(original);
        CGDisplayModeRelease(target);
        return saved ? 0 : (error == kCGErrorSuccess ? 0 : 8);
    }
}
