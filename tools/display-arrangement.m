#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static BOOL parse_integer(const char *text, long *value) {
    char *end = NULL;
    errno = 0;
    long parsed = strtol(text, &end, 10);
    if (errno || end == text || *end != '\0') return NO;
    *value = parsed;
    return YES;
}

static void print_error(const char *message, CGError error) {
    fprintf(stderr, "%s (CoreGraphics error %d).\n", message, (int)error);
}

static int write_current_layout(void) {
    CGError error = kCGErrorSuccess;
    uint32_t count = 0;
    error = CGGetActiveDisplayList(0, NULL, &count);
    if (error != kCGErrorSuccess) {
        print_error("Could not read active displays", error);
        return 7;
    }
    if (count == 0) {
        fputs("[]\n", stdout);
        return 0;
    }
    CGDirectDisplayID *displays = calloc(count, sizeof(CGDirectDisplayID));
    if (!displays) {
        fprintf(stderr, "Out of memory while reading the display layout.\n");
        return 7;
    }
    error = CGGetActiveDisplayList(count, displays, &count);
    if (error != kCGErrorSuccess) {
        free(displays);
        print_error("Could not read active displays", error);
        return 7;
    }
    NSMutableArray *result = [NSMutableArray arrayWithCapacity:count];
    for (uint32_t index = 0; index < count; index++) {
        CGDirectDisplayID current = displays[index];
        CGRect bounds = CGDisplayBounds(current);
        [result addObject:@{
            @"displayId": [NSString stringWithFormat:@"%u", current],
            @"x": @((int32_t)CGRectGetMinX(bounds)),
            @"y": @((int32_t)CGRectGetMinY(bounds)),
            @"width": @((uint32_t)CGRectGetWidth(bounds)),
            @"height": @((uint32_t)CGRectGetHeight(bounds)),
            @"main": @(CGDisplayIsMain(current)),
            @"mirrorsDisplayID": @(CGDisplayMirrorsDisplay(current)),
        }];
    }
    free(displays);
    NSData *json = [NSJSONSerialization dataWithJSONObject:result options:0 error:nil];
    if (!json) {
        fprintf(stderr, "Could not encode the current display layout.\n");
        return 7;
    }
    fwrite(json.bytes, 1, json.length, stdout);
    fputc('\n', stdout);
    return 0;
}

static int set_primary_display(CGDirectDisplayID target) {
    // Hardware-mirrored receivers can be online without appearing in the
    // active display list. Resolve their mirror chain before looking up the
    // active layout; changing the primary of a mirror group means promoting
    // its active master, not the physical receiver.
    uint32_t online_count = 0;
    CGError online_error = CGGetOnlineDisplayList(0, NULL, &online_count);
    if (online_error != kCGErrorSuccess || online_count == 0) {
        print_error("Could not read online displays", online_error);
        return 7;
    }
    CGDirectDisplayID *online_displays = calloc(online_count, sizeof(CGDirectDisplayID));
    if (!online_displays) {
        fprintf(stderr, "Out of memory while resolving the selected display.\n");
        return 7;
    }
    online_error = CGGetOnlineDisplayList(online_count, online_displays, &online_count);
    if (online_error != kCGErrorSuccess) {
        free(online_displays);
        print_error("Could not read online displays", online_error);
        return 7;
    }
    BOOL target_online = NO;
    for (uint32_t index = 0; index < online_count; index++)
        target_online |= online_displays[index] == target;
    if (!target_online || !CGDisplayIsOnline(target)) {
        free(online_displays);
        fprintf(stderr, "The selected display is no longer online.\n");
        return 3;
    }
    CGDirectDisplayID primary_target = target;
    for (uint32_t pass = 0; pass < online_count; pass++) {
        CGDirectDisplayID next = CGDisplayMirrorsDisplay(primary_target);
        if (next == kCGNullDirectDisplay || next == primary_target) break;
        BOOL next_online = NO;
        for (uint32_t index = 0; index < online_count; index++)
            next_online |= online_displays[index] == next;
        if (!next_online) break;
        primary_target = next;
    }
    free(online_displays);

    uint32_t count = 0;
    CGError error = CGGetActiveDisplayList(0, NULL, &count);
    if (error != kCGErrorSuccess || count == 0) {
        print_error("Could not read active displays", error);
        return 7;
    }
    CGDirectDisplayID *displays = calloc(count, sizeof(CGDirectDisplayID));
    CGPoint *origins = calloc(count, sizeof(CGPoint));
    CGDirectDisplayID *mirror_targets = calloc(count, sizeof(CGDirectDisplayID));
    if (!displays || !origins || !mirror_targets) {
        free(displays);
        free(origins);
        free(mirror_targets);
        fprintf(stderr, "Out of memory while preparing the primary display change.\n");
        return 7;
    }
    error = CGGetActiveDisplayList(count, displays, &count);
    if (error != kCGErrorSuccess) {
        free(displays);
        free(origins);
        free(mirror_targets);
        print_error("Could not read active displays", error);
        return 7;
    }
    BOOL found = NO;
    for (uint32_t index = 0; index < count; index++) {
        if (!CGDisplayIsOnline(displays[index]) || !CGDisplayIsActive(displays[index])) {
            free(displays);
            free(origins);
            free(mirror_targets);
            fprintf(stderr, "Primary display changes require active displays.\n");
            return 3;
        }
        CGRect bounds = CGDisplayBounds(displays[index]);
        origins[index] = CGPointMake(CGRectGetMinX(bounds), CGRectGetMinY(bounds));
        mirror_targets[index] = CGDisplayMirrorsDisplay(displays[index]);
        found |= displays[index] == primary_target;
    }
    if (!found) {
        free(displays);
        free(origins);
        free(mirror_targets);
        fprintf(stderr, "The selected display is no longer online and active.\n");
        return 3;
    }

    // The active list supplies geometry and the root's existing mirror links.
    BOOL found_master = NO;
    for (uint32_t index = 0; index < count; index++) {
        found_master |= displays[index] == primary_target;
    }
    if (!found_master || CGDisplayMirrorsDisplay(primary_target) != kCGNullDirectDisplay) {
        free(displays);
        free(origins);
        free(mirror_targets);
        fprintf(stderr, "Could not identify the active master of the selected mirror group.\n");
        return 3;
    }

    CGPoint target_origin = CGPointZero;
    for (uint32_t index = 0; index < count; index++) {
        if (displays[index] == primary_target) target_origin = origins[index];
    }

    CGDisplayConfigRef config = NULL;
    error = CGBeginDisplayConfiguration(&config);
    if (error != kCGErrorSuccess || !config) {
        free(displays);
        free(origins);
        free(mirror_targets);
        print_error("Could not begin a display configuration", error);
        return 4;
    }
    // Re-anchor independent displays to the requested mirror master while
    // preserving every existing mirror link.
    for (uint32_t pass = 0; pass < 2; pass++) {
        for (uint32_t index = 0; index < count; index++) {
            if (mirror_targets[index] != kCGNullDirectDisplay) continue;
            BOOL is_target = displays[index] == primary_target;
            if ((pass == 0) != is_target) continue;
            int64_t x = (int64_t)llround(origins[index].x - target_origin.x);
            int64_t y = (int64_t)llround(origins[index].y - target_origin.y);
            if (x < INT32_MIN || x > INT32_MAX || y < INT32_MIN || y > INT32_MAX) {
                CGCancelDisplayConfiguration(config);
                free(displays);
                free(origins);
                free(mirror_targets);
                fprintf(stderr, "The requested primary arrangement exceeds coordinate limits.\n");
                return 2;
            }
            error = CGConfigureDisplayOrigin(config, displays[index],
                                             (int32_t)x, (int32_t)y);
            if (error != kCGErrorSuccess) {
                CGCancelDisplayConfiguration(config);
                free(displays);
                free(origins);
                free(mirror_targets);
                print_error("macOS rejected the primary display arrangement", error);
                return 5;
            }
        }
    }
    for (uint32_t index = 0; index < count; index++) {
        if (mirror_targets[index] == kCGNullDirectDisplay) continue;
        error = CGConfigureDisplayMirrorOfDisplay(config, displays[index],
                                                  mirror_targets[index]);
        if (error != kCGErrorSuccess) {
            CGCancelDisplayConfiguration(config);
            free(displays);
            free(origins);
            free(mirror_targets);
            print_error("macOS rejected the mirrored primary arrangement", error);
            return 5;
        }
    }
    error = CGCompleteDisplayConfiguration(config, kCGConfigurePermanently);
    if (error != kCGErrorSuccess) {
        free(displays);
        free(origins);
        free(mirror_targets);
        print_error("Could not apply the primary display arrangement", error);
        return 6;
    }

    BOOL mirrors_preserved = YES;
    for (uint32_t index = 0; index < count; index++) {
        if (CGDisplayMirrorsDisplay(displays[index]) != mirror_targets[index]) {
            mirrors_preserved = NO;
            break;
        }
    }
    if (!mirrors_preserved || CGMainDisplayID() != primary_target) {
        CGDisplayConfigRef rollback = NULL;
        CGError rollback_error = CGBeginDisplayConfiguration(&rollback);
        if (rollback_error == kCGErrorSuccess && rollback) {
            for (uint32_t index = 0; index < count; index++) {
                if (mirror_targets[index] != kCGNullDirectDisplay) continue;
                rollback_error = CGConfigureDisplayOrigin(
                    rollback, displays[index], (int32_t)llround(origins[index].x),
                    (int32_t)llround(origins[index].y));
                if (rollback_error != kCGErrorSuccess) break;
            }
            for (uint32_t index = 0; rollback_error == kCGErrorSuccess && index < count; index++) {
                if (mirror_targets[index] == kCGNullDirectDisplay) continue;
                rollback_error = CGConfigureDisplayMirrorOfDisplay(
                    rollback, displays[index], mirror_targets[index]);
            }
            if (rollback_error == kCGErrorSuccess) {
                rollback_error = CGCompleteDisplayConfiguration(
                    rollback, kCGConfigurePermanently);
            } else {
                CGCancelDisplayConfiguration(rollback);
            }
        }
        free(displays);
        free(origins);
        free(mirror_targets);
        fprintf(stderr, "macOS did not apply the requested primary display and preserve the mirror groups.\n");
        if (rollback_error != kCGErrorSuccess) {
            print_error("Could not restore the previous display arrangement", rollback_error);
        }
        return 8;
    }
    free(displays);
    free(origins);
    free(mirror_targets);
    return write_current_layout();
}

static int display_index_for_id(const CGDirectDisplayID *displays, uint32_t count,
                                CGDirectDisplayID display) {
    for (uint32_t index = 0; index < count; index++) {
        if (displays[index] == display) return (int)index;
    }
    return -1;
}

static CGDirectDisplayID mirror_root_for_id(const CGDirectDisplayID *displays,
                                           const CGDirectDisplayID *mirror_targets,
                                           uint32_t count, CGDirectDisplayID display) {
    for (uint32_t pass = 0; pass < count; pass++) {
        int index = display_index_for_id(displays, count, display);
        if (index < 0 || mirror_targets[index] == kCGNullDirectDisplay ||
            mirror_targets[index] == display) break;
        display = mirror_targets[index];
    }
    return display;
}

static int apply_display_role(CGDirectDisplayID display, const char *role,
                              CGDirectDisplayID mirror_source) {
    uint32_t count = 0;
    // The online list also contains hardware-mirrored receivers, which are
    // intentionally absent from CGGetActiveDisplayList on some Macs.
    CGError error = CGGetOnlineDisplayList(0, NULL, &count);
    if (error != kCGErrorSuccess || count == 0) {
        print_error("Could not read online displays", error);
        return 7;
    }
    CGDirectDisplayID *displays = calloc(count, sizeof(CGDirectDisplayID));
    CGPoint *origins = calloc(count, sizeof(CGPoint));
    CGPoint *old_origins = calloc(count, sizeof(CGPoint));
    CGDirectDisplayID *old_targets = calloc(count, sizeof(CGDirectDisplayID));
    CGDirectDisplayID *new_targets = calloc(count, sizeof(CGDirectDisplayID));
    BOOL *configure_origins = calloc(count, sizeof(BOOL));
    if (!displays || !origins || !old_origins || !old_targets || !new_targets || !configure_origins) {
        free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
        fprintf(stderr, "Out of memory while preparing the display role change.\n");
        return 7;
    }
    error = CGGetOnlineDisplayList(count, displays, &count);
    if (error != kCGErrorSuccess) {
        free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
        print_error("Could not read online displays", error);
        return 7;
    }
    int selected_index = display_index_for_id(displays, count, display);
    if (selected_index < 0 || !CGDisplayIsOnline(display)) {
        free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
        fprintf(stderr, "The selected display is no longer online.\n");
        return 3;
    }
    for (uint32_t index = 0; index < count; index++) {
        old_targets[index] = CGDisplayMirrorsDisplay(displays[index]);
        new_targets[index] = old_targets[index];
    }
    for (uint32_t index = 0; index < count; index++) {
        CGDirectDisplayID geometry_display = displays[index];
        if (!CGDisplayIsActive(geometry_display))
            geometry_display = mirror_root_for_id(displays, old_targets, count, geometry_display);
        CGRect bounds = CGDisplayBounds(geometry_display);
        origins[index] = CGPointMake(CGRectGetMinX(bounds), CGRectGetMinY(bounds));
        old_origins[index] = origins[index];
        configure_origins[index] = CGDisplayIsActive(displays[index]);
    }

    CGDirectDisplayID old_root = mirror_root_for_id(displays, old_targets, count, display);
    CGDirectDisplayID old_main = CGMainDisplayID();
    CGDirectDisplayID requested_main = old_main;
    BOOL make_main = NO;
    if (strcmp(role, "extended") == 0) {
        new_targets[selected_index] = kCGNullDirectDisplay;
        // A newly extended output is placed next to the screen it previously
        // mirrored, rather than remaining coincident with it.
        int root_index = display_index_for_id(displays, count, old_root);
        if (root_index >= 0 && display != old_root) {
            CGRect root_bounds = CGDisplayBounds(old_root);
            origins[selected_index] = CGPointMake(CGRectGetMaxX(root_bounds),
                                                   CGRectGetMinY(root_bounds));
            configure_origins[selected_index] = YES;
        }
        if (display == CGMainDisplayID()) {
            // “Extended Display” cannot leave the selected output as the main
            // display. Promote the nearest other independent display in the
            // same transaction so the role change is atomic.
            double best_distance = HUGE_VAL;
            int best_index = -1;
            for (uint32_t index = 0; index < count; index++) {
                if ((int)index == selected_index || !CGDisplayIsActive(displays[index]) ||
                    old_targets[index] != kCGNullDirectDisplay) continue;
                double dx = origins[index].x - origins[selected_index].x;
                double dy = origins[index].y - origins[selected_index].y;
                double distance = dx * dx + dy * dy;
                if (distance < best_distance) { best_distance = distance; best_index = (int)index; }
            }
            BOOL promote_mirror_member = NO;
            if (best_index < 0) {
                // If every other output mirrors the selected main display,
                // promote the nearest member of that group to become the new
                // main display instead of rejecting a valid role change.
                for (uint32_t index = 0; index < count; index++) {
                    if ((int)index == selected_index || !CGDisplayIsOnline(displays[index]) ||
                        mirror_root_for_id(displays, old_targets, count, displays[index]) != display)
                        continue;
                    double dx = origins[index].x - origins[selected_index].x;
                    double dy = origins[index].y - origins[selected_index].y;
                    double distance = dx * dx + dy * dy;
                    if (distance < best_distance) { best_distance = distance; best_index = (int)index; }
                }
                promote_mirror_member = best_index >= 0;
            }
            if (best_index < 0) {
                free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
                fprintf(stderr, "Another independent display is required before the main display can be extended.\n");
                return 3;
            }
            requested_main = displays[best_index];
            make_main = YES;
            if (promote_mirror_member) {
                new_targets[best_index] = kCGNullDirectDisplay;
                CGRect promoted_bounds = CGDisplayBounds(displays[best_index]);
                origins[selected_index] = CGPointMake(CGRectGetMaxX(promoted_bounds),
                                                       CGRectGetMinY(promoted_bounds));
                configure_origins[best_index] = YES;
                configure_origins[selected_index] = YES;
            }
        }
    } else if (strcmp(role, "mirror") == 0) {
        int source_index = display_index_for_id(displays, count, mirror_source);
        if (source_index < 0 || !CGDisplayIsOnline(mirror_source)) {
            free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
            fprintf(stderr, "The selected mirror source is no longer online.\n");
            return 3;
        }
        CGDirectDisplayID source_root = mirror_root_for_id(displays, old_targets, count,
                                                           mirror_source);
        source_index = display_index_for_id(displays, count, source_root);
        if (source_root == old_root || source_index < 0 || !CGDisplayIsActive(source_root)) {
            free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
            fprintf(stderr, "Choose a display outside the selected mirror group.\n");
            return 3;
        }
        new_targets[selected_index] = source_root;
        if (display == CGMainDisplayID()) {
            requested_main = source_root;
            make_main = YES;
        }
    } else {
        free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
        fprintf(stderr, "Unknown display role.\n");
        return 2;
    }

    if (make_main) {
        int main_index = display_index_for_id(displays, count, requested_main);
        if (main_index < 0) {
            free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
            fprintf(stderr, "Could not identify the new main display.\n");
            return 3;
        }
        CGPoint anchor = origins[main_index];
        for (uint32_t index = 0; index < count; index++) {
            if (new_targets[index] != kCGNullDirectDisplay) continue;
            if (!CGDisplayIsActive(displays[index]) && displays[index] != requested_main) continue;
            origins[index].x -= anchor.x;
            origins[index].y -= anchor.y;
            configure_origins[index] = YES;
        }
    }

    CGDisplayConfigRef config = NULL;
    error = CGBeginDisplayConfiguration(&config);
    if (error != kCGErrorSuccess || !config) {
        free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
        print_error("Could not begin a display configuration", error);
        return 4;
    }
    // First detach/promote mirror receivers, then assign their independent
    // origins in the same transaction.
    for (uint32_t index = 0; index < count; index++) {
        if (new_targets[index] == old_targets[index]) continue;
        error = CGConfigureDisplayMirrorOfDisplay(config, displays[index], new_targets[index]);
        if (error != kCGErrorSuccess) break;
    }
    for (uint32_t index = 0; error == kCGErrorSuccess && index < count; index++) {
        if (!configure_origins[index] || new_targets[index] != kCGNullDirectDisplay) continue;
        error = CGConfigureDisplayOrigin(config, displays[index],
                                         (int32_t)llround(origins[index].x),
                                         (int32_t)llround(origins[index].y));
    }
    if (error != kCGErrorSuccess) {
        CGCancelDisplayConfiguration(config);
        free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
        print_error("macOS rejected the requested display role", error);
        return 5;
    }
    error = CGCompleteDisplayConfiguration(config, kCGConfigurePermanently);
    if (error != kCGErrorSuccess) {
        free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
        print_error("Could not apply the requested display role", error);
        return 6;
    }
    BOOL verified = YES;
    for (uint32_t index = 0; index < count; index++) {
        verified &= CGDisplayMirrorsDisplay(displays[index]) == new_targets[index];
    }
    verified &= CGMainDisplayID() == (make_main ? requested_main : old_main);
    if (!verified) {
        CGDisplayConfigRef rollback = NULL;
        CGError rollback_error = CGBeginDisplayConfiguration(&rollback);
        if (rollback_error == kCGErrorSuccess && rollback) {
            for (uint32_t index = 0; index < count; index++) {
                if (!configure_origins[index] || old_targets[index] != kCGNullDirectDisplay) continue;
                rollback_error = CGConfigureDisplayOrigin(
                    rollback, displays[index], (int32_t)llround(old_origins[index].x),
                    (int32_t)llround(old_origins[index].y));
                if (rollback_error != kCGErrorSuccess) break;
            }
            for (uint32_t index = 0; rollback_error == kCGErrorSuccess && index < count; index++) {
                if (old_targets[index] == new_targets[index]) continue;
                rollback_error = CGConfigureDisplayMirrorOfDisplay(
                    rollback, displays[index], old_targets[index]);
            }
            if (rollback_error == kCGErrorSuccess)
                rollback_error = CGCompleteDisplayConfiguration(rollback, kCGConfigurePermanently);
            else
                CGCancelDisplayConfiguration(rollback);
        }
        free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
        fprintf(stderr, "macOS did not apply the requested display role and topology.\n");
        if (rollback_error != kCGErrorSuccess)
            print_error("Could not restore the previous display arrangement", rollback_error);
        return 8;
    }
    free(displays); free(origins); free(old_origins); free(old_targets); free(new_targets); free(configure_origins);
    return write_current_layout();
}

static int move_layout_with_primary_anchor(int32_t delta_x, int32_t delta_y) {
    uint32_t count = 0;
    CGError error = CGGetActiveDisplayList(0, NULL, &count);
    if (error != kCGErrorSuccess || count < 2) {
        fprintf(stderr, "At least two active displays are required to move the primary display arrangement.\n");
        return 3;
    }
    CGDirectDisplayID *displays = calloc(count, sizeof(CGDirectDisplayID));
    if (!displays) {
        fprintf(stderr, "Out of memory while preparing the display arrangement.\n");
        return 7;
    }
    CGPoint *origins = calloc(count, sizeof(CGPoint));
    CGDirectDisplayID *mirror_targets = calloc(count, sizeof(CGDirectDisplayID));
    if (!origins || !mirror_targets) {
        free(displays);
        free(origins);
        free(mirror_targets);
        fprintf(stderr, "Out of memory while preparing the display arrangement.\n");
        return 7;
    }
    error = CGGetActiveDisplayList(count, displays, &count);
    if (error != kCGErrorSuccess) {
        free(displays);
        free(origins);
        free(mirror_targets);
        print_error("Could not read active displays", error);
        return 7;
    }
    CGDirectDisplayID main_display = CGMainDisplayID();
    BOOL found_main = NO;
    for (uint32_t index = 0; index < count; index++) {
        CGRect bounds = CGDisplayBounds(displays[index]);
        origins[index] = CGPointMake(CGRectGetMinX(bounds), CGRectGetMinY(bounds));
        mirror_targets[index] = CGDisplayMirrorsDisplay(displays[index]);
        found_main |= displays[index] == main_display;
    }
    if (!found_main) {
        free(displays);
        free(origins);
        free(mirror_targets);
        fprintf(stderr, "Could not identify the current primary display.\n");
        return 3;
    }

    CGDisplayConfigRef config = NULL;
    error = CGBeginDisplayConfiguration(&config);
    if (error != kCGErrorSuccess || !config) {
        free(displays);
        free(origins);
        free(mirror_targets);
        print_error("Could not begin a display configuration", error);
        return 4;
    }
    for (uint32_t index = 0; index < count; index++) {
        if (displays[index] == main_display || mirror_targets[index] != kCGNullDirectDisplay) continue;
        int64_t new_x = (int64_t)llround(origins[index].x) - delta_x;
        int64_t new_y = (int64_t)llround(origins[index].y) - delta_y;
        if (new_x < INT32_MIN || new_x > INT32_MAX ||
            new_y < INT32_MIN || new_y > INT32_MAX) {
            CGCancelDisplayConfiguration(config);
            free(displays);
            free(origins);
            free(mirror_targets);
            fprintf(stderr, "The requested display arrangement exceeds coordinate limits.\n");
            return 2;
        }
        error = CGConfigureDisplayOrigin(config, displays[index],
                                         (int32_t)new_x, (int32_t)new_y);
        if (error != kCGErrorSuccess) {
            CGCancelDisplayConfiguration(config);
            free(displays);
            free(origins);
            free(mirror_targets);
            print_error("macOS rejected the primary display arrangement", error);
            return 5;
        }
    }
    for (uint32_t index = 0; index < count; index++) {
        if (mirror_targets[index] == kCGNullDirectDisplay) continue;
        error = CGConfigureDisplayMirrorOfDisplay(config, displays[index], mirror_targets[index]);
        if (error != kCGErrorSuccess) {
            CGCancelDisplayConfiguration(config);
            free(displays);
            free(origins);
            free(mirror_targets);
            print_error("macOS rejected the mirrored display arrangement", error);
            return 5;
        }
    }
    error = CGCompleteDisplayConfiguration(config, kCGConfigurePermanently);
    if (error != kCGErrorSuccess) {
        free(displays);
        free(origins);
        free(mirror_targets);
        print_error("Could not apply the primary display arrangement", error);
        return 6;
    }
    BOOL mirrors_preserved = YES;
    for (uint32_t index = 0; index < count; index++) {
        if (CGDisplayMirrorsDisplay(displays[index]) != mirror_targets[index]) {
            mirrors_preserved = NO;
            break;
        }
    }
    if (!mirrors_preserved) {
        CGDisplayConfigRef rollback = NULL;
        CGError rollback_error = CGBeginDisplayConfiguration(&rollback);
        if (rollback_error == kCGErrorSuccess && rollback) {
            for (uint32_t index = 0; index < count; index++) {
                rollback_error = CGConfigureDisplayOrigin(
                    rollback, displays[index], (int32_t)llround(origins[index].x),
                    (int32_t)llround(origins[index].y));
                if (rollback_error != kCGErrorSuccess) break;
            }
            for (uint32_t index = 0; rollback_error == kCGErrorSuccess && index < count; index++) {
                rollback_error = CGConfigureDisplayMirrorOfDisplay(
                    rollback, displays[index], mirror_targets[index]);
            }
            if (rollback_error == kCGErrorSuccess) {
                rollback_error = CGCompleteDisplayConfiguration(
                    rollback, kCGConfigurePermanently);
            } else {
                CGCancelDisplayConfiguration(rollback);
            }
        }
        free(displays);
        free(origins);
        free(mirror_targets);
        fprintf(stderr, "macOS changed the display layout without preserving its mirror groups.\n");
        if (rollback_error != kCGErrorSuccess) {
            print_error("Could not restore the previous display arrangement", rollback_error);
        }
        return 8;
    }
    free(displays);
    free(origins);
    free(mirror_targets);
    if (CGMainDisplayID() != main_display) {
        fprintf(stderr, "The primary display changed unexpectedly while applying the arrangement.\n");
        return 8;
    }
    return write_current_layout();
}

static int move_mirror_group(CGDirectDisplayID master, int32_t x, int32_t y,
                             const CGDirectDisplayID *requested_members,
                             uint32_t requested_member_count) {
    uint32_t active_count = 0;
    CGError error = CGGetActiveDisplayList(0, NULL, &active_count);
    if (error != kCGErrorSuccess || active_count == 0) {
        print_error("Could not read active displays", error);
        return 7;
    }
    uint32_t capacity = active_count + requested_member_count + 1;
    CGDirectDisplayID *displays = calloc(capacity, sizeof(CGDirectDisplayID));
    CGPoint *origins = calloc(capacity, sizeof(CGPoint));
    CGDirectDisplayID *mirror_targets = calloc(capacity, sizeof(CGDirectDisplayID));
    if (!displays || !origins || !mirror_targets) {
        free(displays);
        free(origins);
        free(mirror_targets);
        fprintf(stderr, "Out of memory while preparing the mirrored display group.\n");
        return 7;
    }
    error = CGGetActiveDisplayList(active_count, displays, &active_count);
    if (error != kCGErrorSuccess) {
        free(displays);
        free(origins);
        free(mirror_targets);
        print_error("Could not read active displays", error);
        return 7;
    }
    uint32_t display_count = active_count;
    BOOL found_master = NO;
    for (uint32_t index = 0; index < active_count; index++) {
        CGRect bounds = CGDisplayBounds(displays[index]);
        origins[index] = CGPointMake(CGRectGetMinX(bounds), CGRectGetMinY(bounds));
        mirror_targets[index] = CGDisplayMirrorsDisplay(displays[index]);
        found_master |= displays[index] == master;
    }
    if (!found_master && CGDisplayIsOnline(master) && CGDisplayIsActive(master)) {
        CGRect bounds = CGDisplayBounds(master);
        displays[display_count] = master;
        origins[display_count] = CGPointMake(CGRectGetMinX(bounds), CGRectGetMinY(bounds));
        mirror_targets[display_count] = CGDisplayMirrorsDisplay(master);
        display_count++;
        found_master = YES;
    }
    uint32_t member_count = 0;
    for (uint32_t member_index = 0; member_index < requested_member_count; member_index++) {
        CGDirectDisplayID member = requested_members[member_index];
        if (member == master || !CGDisplayIsOnline(member) ||
            CGDisplayMirrorsDisplay(member) != master) {
            free(displays);
            free(origins);
            free(mirror_targets);
            fprintf(stderr, "A display in the selected mirror group is no longer available; refresh the display list and try again.\n");
            return 3;
        }
        BOOL already_added = NO;
        for (uint32_t index = 0; index < display_count; index++) {
            already_added |= displays[index] == member;
        }
        if (!already_added) {
            CGRect bounds = CGDisplayBounds(member);
            displays[display_count] = member;
            origins[display_count] = CGPointMake(CGRectGetMinX(bounds), CGRectGetMinY(bounds));
            mirror_targets[display_count] = CGDisplayMirrorsDisplay(member);
            display_count++;
        }
        member_count++;
    }
    if (!found_master || CGDisplayMirrorsDisplay(master) != kCGNullDirectDisplay ||
        member_count == 0) {
        free(displays);
        free(origins);
        free(mirror_targets);
        fprintf(stderr, "The selected display is not the active master of the reported mirror group.\n");
        return 3;
    }

    CGDisplayConfigRef config = NULL;
    error = CGBeginDisplayConfiguration(&config);
    if (error != kCGErrorSuccess || !config) {
        free(displays);
        free(origins);
        free(mirror_targets);
        print_error("Could not begin a display configuration", error);
        return 4;
    }
    error = CGConfigureDisplayOrigin(config, master, x, y);
    for (uint32_t index = 0; error == kCGErrorSuccess && index < display_count; index++) {
        if (mirror_targets[index] == master) {
            error = CGConfigureDisplayMirrorOfDisplay(config, displays[index], master);
        }
    }
    if (error != kCGErrorSuccess) {
        CGCancelDisplayConfiguration(config);
        free(displays);
        free(origins);
        free(mirror_targets);
        print_error("macOS rejected the mirrored display group position", error);
        return 5;
    }
    error = CGCompleteDisplayConfiguration(config, kCGConfigurePermanently);
    if (error != kCGErrorSuccess) {
        free(displays);
        free(origins);
        free(mirror_targets);
        print_error("Could not apply the mirrored display group position", error);
        return 6;
    }

    BOOL mirrors_preserved = YES;
    for (uint32_t index = 0; index < display_count; index++) {
        if (mirror_targets[index] == master &&
            CGDisplayMirrorsDisplay(displays[index]) != master) {
            mirrors_preserved = NO;
            break;
        }
    }
    if (!mirrors_preserved) {
        CGDisplayConfigRef rollback = NULL;
        CGError rollback_error = CGBeginDisplayConfiguration(&rollback);
        if (rollback_error == kCGErrorSuccess && rollback) {
            for (uint32_t index = 0; index < display_count; index++) {
                rollback_error = CGConfigureDisplayOrigin(
                    rollback, displays[index], (int32_t)llround(origins[index].x),
                    (int32_t)llround(origins[index].y));
                if (rollback_error != kCGErrorSuccess) break;
            }
            for (uint32_t index = 0; rollback_error == kCGErrorSuccess && index < display_count; index++) {
                if (mirror_targets[index] == kCGNullDirectDisplay) continue;
                rollback_error = CGConfigureDisplayMirrorOfDisplay(
                    rollback, displays[index], mirror_targets[index]);
            }
            if (rollback_error == kCGErrorSuccess) {
                rollback_error = CGCompleteDisplayConfiguration(
                    rollback, kCGConfigurePermanently);
            } else {
                CGCancelDisplayConfiguration(rollback);
            }
        }
        free(displays);
        free(origins);
        free(mirror_targets);
        fprintf(stderr, "macOS moved the display but did not preserve its mirror group.\n");
        if (rollback_error != kCGErrorSuccess) {
            print_error("Could not restore the previous display arrangement", rollback_error);
        }
        return 8;
    }
    free(displays);
    free(origins);
    free(mirror_targets);
    return write_current_layout();
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 1) return write_current_layout();

        if ((argc == 4 || argc == 5) && strcmp(argv[1], "--role") == 0) {
            long raw_display_id = 0, raw_source_id = 0;
            if (!parse_integer(argv[2], &raw_display_id) || raw_display_id <= 0 ||
                raw_display_id > UINT32_MAX) {
                fprintf(stderr, "Expected --role followed by an active display ID and role.\n");
                return 2;
            }
            if (strcmp(argv[3], "main") == 0 && argc == 4)
                return set_primary_display((CGDirectDisplayID)raw_display_id);
            if (strcmp(argv[3], "extended") == 0 && argc == 4)
                return apply_display_role((CGDirectDisplayID)raw_display_id, "extended",
                                          kCGNullDirectDisplay);
            if (strcmp(argv[3], "mirror") == 0 && argc == 5 &&
                parse_integer(argv[4], &raw_source_id) && raw_source_id > 0 &&
                raw_source_id <= UINT32_MAX)
                return apply_display_role((CGDirectDisplayID)raw_display_id, "mirror",
                                          (CGDirectDisplayID)raw_source_id);
            fprintf(stderr, "Expected a role of main, extended, or mirror followed by a source display ID.\n");
            return 2;
        }

        if (argc == 3 && strcmp(argv[1], "--primary") == 0) {
            long raw_display_id = 0;
            if (!parse_integer(argv[2], &raw_display_id) || raw_display_id <= 0 ||
                raw_display_id > UINT32_MAX) {
                fprintf(stderr, "Expected --primary followed by an active display ID.\n");
                return 2;
            }
            return set_primary_display((CGDirectDisplayID)raw_display_id);
        }

        if (argc == 4 && strcmp(argv[1], "--primary-offset") == 0) {
            long raw_x = 0, raw_y = 0;
            if (!parse_integer(argv[2], &raw_x) || !parse_integer(argv[3], &raw_y) ||
                raw_x < INT32_MIN || raw_x > INT32_MAX ||
                raw_y < INT32_MIN || raw_y > INT32_MAX) {
                fprintf(stderr, "Expected --primary-offset followed by integer x/y offsets.\n");
                return 2;
            }
            return move_layout_with_primary_anchor((int32_t)raw_x, (int32_t)raw_y);
        }

        if (argc >= 6 && strcmp(argv[1], "--mirror-group") == 0) {
            long raw_display_id = 0, raw_x = 0, raw_y = 0;
            if (!parse_integer(argv[2], &raw_display_id) || raw_display_id <= 0 ||
                raw_display_id > UINT32_MAX || !parse_integer(argv[3], &raw_x) ||
                !parse_integer(argv[4], &raw_y) || raw_x < INT32_MIN ||
                raw_x > INT32_MAX || raw_y < INT32_MIN || raw_y > INT32_MAX) {
                fprintf(stderr, "Expected --mirror-group followed by a master display ID, integer x/y coordinates, and one or more mirrored display IDs.\n");
                return 2;
            }
            uint32_t member_count = (uint32_t)(argc - 5);
            CGDirectDisplayID *members = calloc(member_count, sizeof(CGDirectDisplayID));
            if (!members) {
                fprintf(stderr, "Out of memory while reading mirror group members.\n");
                return 7;
            }
            for (uint32_t index = 0; index < member_count; index++) {
                long raw_member_id = 0;
                if (!parse_integer(argv[index + 5], &raw_member_id) || raw_member_id <= 0 ||
                    raw_member_id > UINT32_MAX || raw_member_id == raw_display_id) {
                    free(members);
                    fprintf(stderr, "Expected valid mirrored display IDs after the mirror group coordinates.\n");
                    return 2;
                }
                members[index] = (CGDirectDisplayID)raw_member_id;
            }
            int result = move_mirror_group((CGDirectDisplayID)raw_display_id,
                                           (int32_t)raw_x, (int32_t)raw_y,
                                           members, member_count);
            free(members);
            return result;
        }

        long raw_display_id = 0, raw_x = 0, raw_y = 0;
        if (argc != 4 || !parse_integer(argv[1], &raw_display_id) ||
            !parse_integer(argv[2], &raw_x) || !parse_integer(argv[3], &raw_y) ||
            raw_display_id <= 0 || raw_display_id > UINT32_MAX ||
            raw_x < INT32_MIN || raw_x > INT32_MAX ||
            raw_y < INT32_MIN || raw_y > INT32_MAX) {
            fprintf(stderr, "Expected no arguments to query, --primary and a display ID, --primary-offset and integer x/y offsets, --mirror-group with a master ID, x/y coordinates, and mirrored display IDs, or a display ID and integer x/y coordinates to set.\n");
            return 2;
        }
        CGDirectDisplayID display = (CGDirectDisplayID)raw_display_id;
        if (!CGDisplayIsOnline(display) || !CGDisplayIsActive(display)) {
            fprintf(stderr, "The selected display is no longer online and active.\n");
            return 3;
        }
        if (CGDisplayIsInMirrorSet(display)) {
            fprintf(stderr, "Arrangement changes are not supported for mirrored displays.\n");
            return 3;
        }

        CGDisplayConfigRef config = NULL;
        CGError error = CGBeginDisplayConfiguration(&config);
        if (error != kCGErrorSuccess || !config) {
            print_error("Could not begin a display configuration", error);
            return 4;
        }
        error = CGConfigureDisplayOrigin(config, display, (int32_t)raw_x, (int32_t)raw_y);
        if (error != kCGErrorSuccess) {
            CGCancelDisplayConfiguration(config);
            print_error("macOS rejected the requested display position", error);
            return 5;
        }
        error = CGCompleteDisplayConfiguration(config, kCGConfigurePermanently);
        if (error != kCGErrorSuccess) {
            print_error("Could not apply the display arrangement", error);
            return 6;
        }
        return write_current_layout();
    }
}
