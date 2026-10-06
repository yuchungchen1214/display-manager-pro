// Read-only IORegistry inventory and per-service EDID acquisition.
// Private declarations: https://gist.github.com/zhuowei/223e449a90a32eefd2c3244e252818d1
#import <Foundation/Foundation.h>
#import <IOKit/IOKitLib.h>
#import <dlfcn.h>

static id safe(id v) {
    if ([v isKindOfClass:NSData.class]) return @{ @"base64":[v base64EncodedStringWithOptions:0] };
    if ([v isKindOfClass:NSDictionary.class]) {
        NSMutableDictionary *d=NSMutableDictionary.new;
        for (id k in v) d[[k description]]=safe(v[k]);
        return d;
    }
    if ([v isKindOfClass:NSArray.class]) {
        NSMutableArray *a=NSMutableArray.new; for (id x in v) [a addObject:safe(x)]; return a;
    }
    if ([v isKindOfClass:NSString.class] || [v isKindOfClass:NSNumber.class]) return v;
    return v ? [v description] : NSNull.null;
}
static NSDictionary *identity(io_registry_entry_t entry) {
    io_string_t path={0}; io_name_t name={0}, cls={0}; uint64_t ident=0;
    IORegistryEntryGetPath(entry,kIOServicePlane,path);
    IORegistryEntryGetName(entry,name); IOObjectGetClass(entry,cls);
    IORegistryEntryGetRegistryEntryID(entry,&ident);
    return @{ @"path":@(path), @"name":@(name), @"class":@(cls),
              @"entryID":[NSString stringWithFormat:@"%llu",(unsigned long long)ident] };
}
static NSDictionary *inventory(void) {
    io_registry_entry_t root=IORegistryGetRootEntry(kIOMainPortDefault);
    io_iterator_t iterator=0;
    kern_return_t rc=IORegistryEntryCreateIterator(root,kIOServicePlane,kIORegistryIterateRecursively,&iterator);
    IOObjectRelease(root);
    NSMutableArray *records=NSMutableArray.new;
    if (rc==KERN_SUCCESS) {
        io_registry_entry_t entry;
        while ((entry=IOIteratorNext(iterator))) {
            NSDictionary *ident=identity(entry);
            NSString *cls=ident[@"class"], *name=ident[@"name"];
            BOOL relevant=[cls containsString:@"DCP"] || [cls containsString:@"CLCD"] ||
                [cls containsString:@"Display"] || [cls containsString:@"Framebuffer"] ||
                [cls containsString:@"Thunderbolt"] || [cls containsString:@"USBHostDevice"] ||
                [cls containsString:@"USBHostPort"] || [cls containsString:@"IOPort"] || [cls containsString:@"TypeC"] ||
                [cls containsString:@"AppleATC"] || [cls containsString:@"DPTX"] ||
                [name hasPrefix:@"atc"] || [name hasPrefix:@"acio"] ||
                [name hasPrefix:@"dispext"] || [name hasPrefix:@"disp0"];
            if (relevant) {
                NSMutableDictionary *record=[ident mutableCopy];
                CFMutableDictionaryRef props=NULL;
                kern_return_t status=IORegistryEntryCreateCFProperties(entry,&props,kCFAllocatorDefault,0);
                record[@"propertiesStatus"]=@(status);
                record[@"properties"]=props ? safe(CFBridgingRelease(props)) : @{};
                NSMutableArray *parents=NSMutableArray.new;
                io_registry_entry_t cursor=entry; IOObjectRetain(cursor);
                for (int depth=0; depth<64; depth++) {
                    io_registry_entry_t parent=0;
                    if (IORegistryEntryGetParentEntry(cursor,kIOServicePlane,&parent)!=KERN_SUCCESS) break;
                    [parents addObject:identity(parent)]; IOObjectRelease(cursor); cursor=parent;
                }
                IOObjectRelease(cursor); record[@"ancestors"]=parents;
                [records addObject:record];
            }
            IOObjectRelease(entry);
        }
        IOObjectRelease(iterator);
    }
    return @{ @"status":rc==0?@"read":@"error", @"returnCode":@(rc), @"services":records };
}
static NSDictionary *edid(uint64_t entryID) {
    io_service_t service=IOServiceGetMatchingService(kIOMainPortDefault,IORegistryEntryIDMatching(entryID));
    if (!service) return @{ @"status":@"service-disappeared" };
    NSDictionary *ident=identity(service);
    // Do not open arbitrary services supplied on the command line.
    if (![ident[@"class"] isEqual:@"DCPAVServiceProxy"]) {
        IOObjectRelease(service); return @{ @"status":@"unsupported-service" };
    }
    void *handle=dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",RTLD_LAZY);
    CFTypeRef (*create)(CFAllocatorRef,io_service_t)=handle?dlsym(handle,"IOAVServiceCreateWithService"):NULL;
    IOReturn (*copy)(CFTypeRef,CFDataRef*)=handle?dlsym(handle,"IOAVServiceCopyEDID"):NULL;
    IOReturn (*read)(CFTypeRef,uint32_t,uint32_t,void*,uint32_t)=handle?dlsym(handle,"IOAVServiceReadI2C"):NULL;
    NSMutableDictionary *result=[ident mutableCopy];
    CFTypeRef av=create?create(kCFAllocatorDefault,service):NULL;
    IOObjectRelease(service);
    result[@"status"]=av?@"opened":@"unavailable";
    if (av) {
        CFDataRef bytes=NULL;
        IOReturn rc=copy?copy(av,&bytes):kIOReturnUnsupported;
        result[@"osEDID"]=@{ @"source":@"IOAVServiceCopyEDID", @"returnCode":@(rc),
            @"status":rc==0 && bytes?@"read":@"unavailable",
            @"data":bytes?safe(CFBridgingRelease(bytes)):NSNull.null };
        // Base and first extension only. No segment-pointer writes are made.
        uint8_t base[128]={0}; rc=read?read(av,0x50,0,base,128):kIOReturnUnsupported;
        NSMutableData *data=NSMutableData.new;
        NSMutableArray *reads=[NSMutableArray arrayWithObject:@{ @"offset":@0,@"returnCode":@(rc) }];
        if (rc==0) {
            [data appendBytes:base length:128];
            if (base[126]>0) {
                uint8_t ext[128]={0}; IOReturn extRC=read(av,0x50,128,ext,128);
                [reads addObject:@{ @"offset":@128,@"returnCode":@(extRC) }];
                if (extRC==0) [data appendBytes:ext length:128];
            }
        }
        result[@"i2cEDID"]=@{ @"source":@"IOAVServiceReadI2C(0x50)",@"status":data.length?@"read":@"unavailable",
            @"reads":reads,@"data":safe(data),@"maxBlocksRead":@2 };
        CFRelease(av);
    }
    if (handle) dlclose(handle);
    return result;
}
int main(int argc,const char **argv) { @autoreleasepool {
    NSDictionary *result=argc==3 && strcmp(argv[1],"--edid")==0 ? edid(strtoull(argv[2],NULL,10)) : inventory();
    NSError *error=nil;
    NSData *json=[NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingPrettyPrinted error:&error];
    if (!json) { fprintf(stderr,"%s\n",error.description.UTF8String); return 1; }
    fwrite(json.bytes,1,json.length,stdout); return 0;
} }
