#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>
#import <objc/runtime.h>
int main(void) { @autoreleasepool {
    for (NSString *name in @[@"CADisplay", @"CADisplayMode"]) {
        Class cls = NSClassFromString(name);
        for (int meta=0;meta<2;meta++) {
            unsigned count=0;
            Method *methods=class_copyMethodList(meta?object_getClass(cls):cls,&count);
            for(unsigned i=0;i<count;i++) printf("%s %c %s %s\n",name.UTF8String,meta?'+':'-',sel_getName(method_getName(methods[i])),method_getTypeEncoding(methods[i]));
            free(methods);
        }
    }
} }
